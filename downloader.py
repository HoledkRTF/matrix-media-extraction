import os
import json
import asyncio
import datetime
import mimetypes
import hashlib
import vodozemac
from nio.responses import RoomMessagesResponse
from nio.events.room_events import RoomMessageMedia, MegolmEvent
from nio.crypto import decrypt_attachment

CACHE_DIR = "cache"

class MediaDownloader:
    def __init__(self, client_wrapper):
        self.client_wrapper = client_wrapper
        self.client = client_wrapper.client
        self.download_dir = "downloads"
        self._megolm_sessions = {}
        self._current_room_sizes = {}
        self._current_room_hashes = {}
        self._existing_files = set()
        self._dedup_lock = asyncio.Lock()

    def load_exported_keys(self, session_map: dict):
        loaded = 0
        for (room_id, session_id), session_key in session_map.items():
            try:
                exported = vodozemac.ExportedSessionKey(session_key)
                session = vodozemac.InboundGroupSession.import_session(exported)
                self._megolm_sessions[(room_id, session_id)] = session
                loaded += 1
            except Exception:
                pass
        return loaded

    def _scan_room_for_dedup(self, room_dir):
        self._current_room_sizes.clear()
        self._current_room_hashes.clear()
        self._existing_files.clear()
        if not os.path.isdir(room_dir):
            return
        for fname in os.listdir(room_dir):
            path = os.path.join(room_dir, fname)
            if os.path.isfile(path):
                self._existing_files.add(fname)
                size = os.path.getsize(path)
                if size not in self._current_room_sizes:
                    self._current_room_sizes[size] = []
                self._current_room_sizes[size].append(path)

    async def delta_scan_room(self, room_id):
        """Scans history backward until it hits the cache bookmark.
        
        Returns (new_media_count, is_cached) where is_cached means the room
        was previously fully scanned and scan_complete was True.
        """
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache_path = os.path.join(CACHE_DIR, f"{room_id.replace('!', '_').replace(':', '_')}.json")
        
        cache = {"room_id": room_id, "last_scanned_event_id": None, "media_list": [], "scan_complete": False}
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                    # Migrate old caches that don't have scan_complete
                    if "scan_complete" not in cache:
                        cache["scan_complete"] = False
            except Exception:
                pass

        bookmark = cache["last_scanned_event_id"]
        was_already_complete = cache.get("scan_complete", False)
        
        room = self.client.rooms.get(room_id)
        if not room:
            return 0, was_already_complete
            
        token = room.timeline.prev_batch if hasattr(room, "timeline") and room.timeline else None
        if not token:
            try:
                resp = await asyncio.wait_for(
                    self.client.room_messages(room_id, direction="b", limit=1),
                    timeout=30.0
                )
                if isinstance(resp, RoomMessagesResponse):
                    token = resp.end
            except asyncio.TimeoutError:
                return 0, was_already_complete

        if not token:
            return 0, was_already_complete

        found_media = []
        newest_event_id = None
        hit_bookmark = False
        timed_out = False
        events_processed = 0

        try:
            while token and not hit_bookmark:
                try:
                    response = await asyncio.wait_for(
                        self.client.room_messages(room_id, start=token, limit=100, direction="b"),
                        timeout=30.0
                    )
                except asyncio.TimeoutError:
                    timed_out = True
                    break

                if not isinstance(response, RoomMessagesResponse):
                    break

                for event in response.chunk:
                    if not newest_event_id:
                        newest_event_id = event.event_id
                    
                    if event.event_id == bookmark:
                        hit_bookmark = True
                        break
                    
                    # --- Extract media from this event ---
                    media_entry = self._extract_media_from_event(room_id, event)
                    if media_entry:
                        found_media.append(media_entry)
                    
                    events_processed += 1
                    
                    # Intermediate save every 500 events (protects against Ctrl+C)
                    if events_processed % 500 == 0 and found_media:
                        self._append_media_to_cache(cache, found_media)
                        found_media.clear()
                        with open(cache_path, "w", encoding="utf-8") as f:
                            json.dump(cache, f)

                if response.end == token:
                    break
                token = response.end

        except Exception:
            timed_out = True

        # Flush any remaining found media into the cache
        if found_media:
            self._append_media_to_cache(cache, found_media)

        # Only update the bookmark if we completed the scan without timing out
        scan_completed = (hit_bookmark or token is None or (response is not None and response.end == token)) and not timed_out
        
        if scan_completed and newest_event_id:
            cache["last_scanned_event_id"] = newest_event_id
            cache["scan_complete"] = True
        elif not scan_completed:
            # Don't move the bookmark — we have a gap. Mark incomplete.
            cache["scan_complete"] = False

        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f)

        new_media_count = len(cache["media_list"]) if not was_already_complete else sum(1 for _ in found_media)
        is_cached = was_already_complete and scan_completed
        return new_media_count, is_cached

    def _extract_media_from_event(self, room_id, event):
        """Extract media metadata from a single event. Returns a dict or None."""
        if isinstance(event, RoomMessageMedia):
            content = event.source.get("content", {})
            info = content.get("info", {})
            return {
                "timestamp_ms": getattr(event, "server_timestamp", None),
                "url": content.get("url"),
                "file_info": content.get("file"),
                "body": event.body or "unknown",
                "size": info.get("size", 0),
                "mimetype": info.get("mimetype", ""),
            }
        elif isinstance(event, MegolmEvent):
            session_id = event.session_id
            session = self._megolm_sessions.get((room_id, session_id))
            if not session:
                return None
            try:
                megolm_msg = vodozemac.MegolmMessage.from_base64(event.ciphertext)
                plaintext = session.decrypt(megolm_msg).plaintext
                event_json = json.loads(plaintext.decode("utf-8"))
                content = event_json.get("content", event_json)
                msgtype = content.get("msgtype", "")
                if msgtype in ("m.image", "m.video", "m.audio", "m.file"):
                    info = content.get("info", {})
                    return {
                        "timestamp_ms": getattr(event, "server_timestamp", None),
                        "url": content.get("url"),
                        "file_info": content.get("file"),
                        "body": content.get("body", "unknown"),
                        "size": info.get("size", 0),
                        "mimetype": info.get("mimetype", ""),
                    }
            except Exception:
                pass
        return None

    def _append_media_to_cache(self, cache, media_entries):
        """Append a list of media entry dicts to the cache's media_list and deduplicate."""
        cache["media_list"].extend(media_entries)
        seen = set()
        unique = []
        for m in cache["media_list"]:
            url = m.get("url") or (m.get("file_info", {}).get("url") if m.get("file_info") else None)
            sig = (m.get("timestamp_ms"), url, m.get("body"))
            if sig not in seen:
                seen.add(sig)
                unique.append(m)
        cache["media_list"] = unique

    async def download_media_from_room(self, room_id, progress_callback=None, only_oldest=False):
        """Downloads all media using the JSON cache instead of API fetching."""
        room = self.client.rooms.get(room_id)
        if not room:
            return
            
        room_name = room.display_name
        room_name = "".join(c for c in room_name if c.isalnum() or c in " ._-[]").strip()
        if not room_name:
            room_name = room_id.replace("!", "_").replace(":", "_")
            
        if only_oldest:
            room_name += "x"
            
        os.makedirs(self.download_dir, exist_ok=True)
        room_dir = os.path.join(self.download_dir, room_name)
        os.makedirs(room_dir, exist_ok=True)
        self._scan_room_for_dedup(room_dir)

        cache_path = os.path.join(CACHE_DIR, f"{room_id.replace('!', '_').replace(':', '_')}.json")
        if not os.path.exists(cache_path):
            return {}
            
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
            
        media_list = cache.get("media_list", [])
        if not media_list:
            return {}
            
        # Deduplicate media list (fixes bloated caches from interrupted scans)
        seen = set()
        unique_media = []
        for m in media_list:
            url = m.get("url") or (m.get("file_info", {}).get("url") if m.get("file_info") else None)
            sig = (m.get("timestamp_ms"), url, m.get("body"))
            if sig not in seen:
                seen.add(sig)
                unique_media.append(m)
        media_list = unique_media

        # Optionally save the cleaned cache back to disk
        cache["media_list"] = media_list
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f)
            
        stats = {
            "downloaded": 0, "downloaded_size": 0,
            "existed": 0, "existed_size": 0,
            "deduped": 0, "deduped_size": 0,
            "scanned": 0, "errors": 0, "errors_size": 0
        }

        if only_oldest:
            # Filter and sort
            valid_media = [m for m in media_list if m.get("timestamp_ms") is not None]
            valid_media.sort(key=lambda x: x["timestamp_ms"])
            for oldest in valid_media:
                body = oldest["body"]
                if progress_callback:
                    progress_callback("start", filename=body)
                    
                status = await self._download_media(oldest["url"], oldest["file_info"], body, room_dir, oldest["timestamp_ms"])
                
                if progress_callback:
                    progress_callback("finish", filename=body)
                    
                size = oldest.get("size") or 0
                if status == "DOWNLOADED":
                    stats["downloaded"] += 1
                    stats["downloaded_size"] += size
                elif status == "EXISTED":
                    stats["existed"] += 1
                    stats["existed_size"] += size
                elif status == "DEDUPED":
                    stats["deduped"] += 1
                    stats["deduped_size"] += size
                elif status == "ERROR":
                    stats["errors"] += 1
                    stats["errors_size"] += size
                    
                if status in ("DOWNLOADED", "EXISTED", "DEDUPED"):
                    # Write the oldest_image_details.txt report
                    timestamp_ms = oldest["timestamp_ms"]
                    dt = datetime.datetime.fromtimestamp(timestamp_ms / 1000.0) if timestamp_ms else None
                    date_str = dt.strftime("%B %d, %Y at %I:%M %p") if dt else "Unknown"
                    info_path = os.path.join(room_dir, "oldest_image_details.txt")
                    with open(info_path, "w", encoding="utf-8") as f:
                        f.write(f"Room: {room.display_name}\n")
                        f.write(f"Original Filename: {body}\n")
                        f.write(f"Date Uploaded: {date_str}\n")
                        f.write(f"Timestamp (ms): {timestamp_ms}\n")
                        f.write(f"Matrix URL: {oldest['url'] or (oldest['file_info'] or {{}}).get('url', 'N/A')}\n")
                        f.write(f"Total Media Found: {len(media_list)}\n")
                    return stats  # Success!
        else:
            # --- Concurrent bulk download with 10 parallel workers ---
            semaphore = asyncio.Semaphore(10)
            stats_lock = asyncio.Lock()
            
            async def download_one(m):
                async with semaphore:
                    body = m["body"]
                    if progress_callback:
                        progress_callback("start", filename=body)
                    
                    status = await self._download_media(m["url"], m["file_info"], body, room_dir, m["timestamp_ms"])
                    
                    size = m.get("size") or 0
                    async with stats_lock:
                        stats["scanned"] += 1
                        if status == "DOWNLOADED":
                            stats["downloaded"] += 1
                            stats["downloaded_size"] += size
                        elif status == "EXISTED":
                            stats["existed"] += 1
                            stats["existed_size"] += size
                        elif status == "DEDUPED":
                            stats["deduped"] += 1
                            stats["deduped_size"] += size
                        elif status == "ERROR":
                            stats["errors"] += 1
                            stats["errors_size"] += size
                        
                        if progress_callback:
                            progress_callback("stats", **stats)
                            progress_callback("finish", filename=body)
            
            await asyncio.gather(*(download_one(m) for m in media_list))
            
        return stats

    async def _download_media(self, url, file_info, body, room_dir, timestamp_ms=None):
        target_url = url or (file_info.get("url") if file_info else None)
        if not target_url or not target_url.startswith("mxc://"):
            return "ERROR"

        server_and_media_id = target_url[6:]
        try:
            server_name, media_id = server_and_media_id.split("/", 1)
        except ValueError:
            return "ERROR"

        filename = body or "file.bin"
        filename = "".join(c for c in filename if c.isalnum() or c in " ._-")
        if not filename:
            filename = "file.bin"
            
        if "." not in filename:
            mime_type = file_info.get("mimetype") if file_info else None
            if mime_type:
                ext = mimetypes.guess_extension(mime_type)
                if ext:
                    filename += ext
        
        if timestamp_ms:
            dt = datetime.datetime.fromtimestamp(timestamp_ms / 1000.0)
            prefix = dt.strftime("%Y%m%d_%H%M%S_")
        else:
            prefix = ""

        filename = filename.replace("/", "_").replace("\\", "_")
        filename = f"{prefix}{media_id}_{filename}"
        filepath = os.path.join(room_dir, filename)

        # O(1) check against pre-built set instead of hitting the filesystem
        if filename in self._existing_files:
            return "EXISTED"

        try:
            resp = await asyncio.wait_for(
                self.client.download(server_name=server_name, media_id=media_id),
                timeout=45.0
            )
        except asyncio.TimeoutError:
            return "ERROR"
        except Exception:
            return "ERROR"
            
        if not resp:
            return "ERROR"
            
        data = getattr(resp, "body", b"")
        if not data:
            return "ERROR"
            
        if file_info:
            data = await asyncio.to_thread(
                decrypt_attachment, data, file_info["key"]["k"], file_info["hashes"]["sha256"], file_info["iv"]
            )
            
        data_size = len(data)
        data_hash = None

        # Pre-compute data hash OUTSIDE the lock if there's a size collision 
        # (prevents blocking all other concurrent downloads)
        if data_size in self._current_room_sizes:
            data_hash = await asyncio.to_thread(lambda d: hashlib.sha256(d).hexdigest(), data)

        # --- Dedup check (narrow lock: only covers hash comparison + dict update) ---
        async with self._dedup_lock:
            if data_size in self._current_room_sizes:
                if data_hash is None:
                    data_hash = await asyncio.to_thread(lambda d: hashlib.sha256(d).hexdigest(), data)
                for existing_path in self._current_room_sizes[data_size]:
                    if existing_path not in self._current_room_hashes:
                        def _read_hash(p):
                            with open(p, "rb") as f:
                                return hashlib.sha256(f.read()).hexdigest()
                        try:
                            h_val = await asyncio.to_thread(_read_hash, existing_path)
                            self._current_room_hashes[existing_path] = h_val
                        except OSError:
                            continue
                    if self._current_room_hashes[existing_path] == data_hash:
                        return "DEDUPED"

            # Register in dedup maps before releasing lock (prevents race conditions)
            if data_size not in self._current_room_sizes:
                self._current_room_sizes[data_size] = []
            self._current_room_sizes[data_size].append(filepath)
            if data_hash is None:
                data_hash = await asyncio.to_thread(lambda d: hashlib.sha256(d).hexdigest(), data)
            self._current_room_hashes[filepath] = data_hash

        # --- File write happens OUTSIDE the lock so other workers aren't blocked ---
        def _write_file(p, d, ts):
            with open(p, "wb") as f:
                f.write(d)
            if ts:
                t_sec = ts / 1000.0
                os.utime(p, (t_sec, t_sec))
        
        await asyncio.to_thread(_write_file, filepath, data, timestamp_ms)
        
        # Add to existing files set for future O(1) lookups
        self._existing_files.add(filename)

        return "DOWNLOADED"
