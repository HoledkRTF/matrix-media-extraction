import asyncio
import json
import logging
import os
from dotenv import load_dotenv

load_dotenv()

# Silence noisy matrix-nio validation errors
logging.getLogger("nio").setLevel(logging.CRITICAL)

from client import MatrixBackupClient
from cli import (
    get_credentials,
    select_rooms,
    console,
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    _format_size,
)
from downloader import MediaDownloader
from key_export import decrypt_key_export, build_session_map
from nio.events.room_events import RoomMessageMedia, MegolmEvent, RoomMessageText


async def main():
    homeserver, username, password = get_credentials()

    client_wrapper = MatrixBackupClient(homeserver, username, password)
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            login_task = progress.add_task("[cyan]Logging in...", total=None)

            success, msg = await client_wrapper.login()

            if not success:
                progress.stop()
                console.print(f"[bold red]Login failed: {msg}[/bold red]")
                return

            progress.update(login_task, description="[cyan]Syncing initial state...")
            await client_wrapper.sync()

        rooms = await client_wrapper.get_rooms()
        if not rooms:
            console.print("[bold red]No rooms found.[/bold red]")
            return

        # --- Key import ---
        import glob

        downloader = MediaDownloader(client_wrapper)
        
        key_file = os.getenv("MATRIX_KEY_FILE_PATH", "")
        
        # Fallback logic if env var is not set or file doesn't exist
        if not key_file or not os.path.exists(key_file):
            downloads_dir = os.path.expanduser("~/Downloads")
            key_files = glob.glob(os.path.join(downloads_dir, "element-keys*.txt"))
            key_file = key_files[0] if key_files else ""

        if key_file and os.path.exists(key_file):
            passphrase = os.getenv("MATRIX_KEY_PASSPHRASE", "")
            try:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                ) as progress:
                    task = progress.add_task(
                        "[cyan]Decrypting key export...", total=None
                    )
                    sessions = decrypt_key_export(key_file.strip(), passphrase)
                    session_map = build_session_map(sessions)
                    loaded = downloader.load_exported_keys(session_map)
                console.print(
                    f"[bold green]Imported {loaded} Megolm sessions from {len(sessions)} exported keys.[/bold green]"
                )
            except Exception as e:
                console.print(f"[bold red]Failed to import keys: {e}[/bold red]")

        # --- Pre-Menu Delta Scanning (Moved to Option 1) ---
        async def perform_delta_scan():
            room_statuses = {}
            scan_semaphore = asyncio.Semaphore(5)
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TextColumn("[green]{task.completed}/{task.total} rooms"),
                console=console,
            ) as progress:
                scan_task = progress.add_task("[cyan]Initializing scans...", total=len(rooms))
                
                async def scan_and_record(r_id):
                    async with scan_semaphore:
                        room_info = rooms.get(r_id, (r_id, False))
                    room_name = room_info[0] if isinstance(room_info, tuple) else room_info
                    progress.update(scan_task, description=f"[cyan]Scanning: {room_name[:45]}")
                    try:
                        count, cached = await downloader.delta_scan_room(r_id)
                        # Read the cache to compute total size and media count
                        cache_path = os.path.join("cache", f"{r_id.replace('!', '_').replace(':', '_')}.json")
                        total_size = 0
                        media_count = 0
                        if os.path.exists(cache_path):
                            with open(cache_path, "r", encoding="utf-8") as f:
                                cache_data = json.load(f)
                            media_list = cache_data.get("media_list", [])
                            media_count = len(media_list)
                            total_size = sum(m.get("size", 0) or 0 for m in media_list)
                        room_statuses[r_id] = (media_count, cached, total_size)
                    except Exception as e:
                        console.print(f"[bold red]Error scanning {room_name}: {e}[/bold red]")
                        room_statuses[r_id] = (None, False, 0)
                    finally:
                        # Update text as rooms finish so it doesn't get stuck!
                        progress.update(scan_task, description=f"[cyan]Finished: {room_name[:45]}")
                        progress.advance(scan_task)
                
                await asyncio.gather(*(scan_and_record(rid) for rid in rooms.keys()))
            return room_statuses

        import argparse
        parser = argparse.ArgumentParser(description="Matrix Media Archiver")
        parser.add_argument("--watch", action="store_true", help="Run in continuous watch mode")
        args = parser.parse_args()

        if args.watch:
            console.print("\n[bold cyan]Entering Watch Mode. Waiting for new messages...[/bold cyan]")
            
            def _handle_event(room, event):
                async def _task():
                    try:
                        await downloader.delta_scan_room(room.room_id)
                        await downloader.download_media_from_room(room.room_id)
                        # Optionally export HTML? We'll add this later if requested
                        # await downloader.export_html(room.room_id)
                    except Exception as e:
                        console.print(f"[bold red]Watch mode error in {room.room_id}: {e}[/bold red]")
                
                asyncio.create_task(_task())
                
            client_wrapper.client.add_event_callback(_handle_event, RoomMessageMedia)
            client_wrapper.client.add_event_callback(_handle_event, MegolmEvent)
            client_wrapper.client.add_event_callback(_handle_event, RoomMessageText)
            
            try:
                await client_wrapper.client.sync_forever(timeout=30000, full_state=True)
            except asyncio.CancelledError:
                pass
            return

        # --- Main loop for selection and downloading ---
        from cli import Prompt
        while True:
            console.print("\n[bold cyan]--- Main Menu ---[/bold cyan]")
            console.print("[1] Download Media (Select Rooms)")
            console.print("[2] Exit")
            console.print("[3] Retroactively Process Old Media (Transcode Videos)")
            choice = Prompt.ask("Select an option", choices=["1", "2", "3"], default="1")
            
            if choice == "2":
                break
            elif choice == "3":
                import retrofill
                await retrofill.process_backlog("downloads")
                continue

            # --- Room selection ---
            room_statuses = await perform_delta_scan()
            selected = select_rooms(rooms, room_statuses)
            if not selected:
                console.print("[bold yellow]No rooms selected. Returning to menu.[/bold yellow]")
                continue

            # --- Download ---
            with Progress(
                SpinnerColumn(),
                "[progress.description]{task.description}",
                console=console,
            ) as progress:
                for room_id, only_oldest in selected.items():
                    room_info = rooms.get(room_id, (room_id, False))
                    room_name = room_info[0] if isinstance(room_info, tuple) else room_info
                    
                    desc_prefix = f"[green]Scanning for Oldest in {room_name}...[/green]" if only_oldest else f"[green]Downloading {room_name}...[/green]"
                    
                    task = progress.add_task(
                        f"{desc_prefix} | Scanned: [cyan]0[/cyan] | Downloaded: [green]0[/green] | Dupes Skipped: [yellow]0[/yellow] | Existed: [blue]0[/blue]", total=None
                    )

                    def make_callback(t, r_name, is_oldest):
                        subtasks = {}
                        def progress_handler(event_type, **kwargs):
                            prefix = f"[green]Scanning for Oldest in {r_name}...[/green]" if is_oldest else f"[green]Downloading {r_name}...[/green]"
                            if event_type == "stats":
                                progress.update(
                                    t,
                                    description=f"{prefix} | Scanned: [cyan]{kwargs['scanned']}[/cyan] | Downloaded: [green]{kwargs['downloaded']}[/green] | Dupes Skipped: [yellow]{kwargs['deduped']}[/yellow] | Existed: [blue]{kwargs['existed']}[/blue]"
                                )
                            elif event_type == "start":
                                filename = kwargs["filename"]
                                if filename not in subtasks:
                                    subtasks[filename] = progress.add_task(f"[dim]  ↳ Processing {filename}...[/dim]", total=None)
                            elif event_type == "finish":
                                filename = kwargs["filename"]
                                if filename in subtasks:
                                    progress.remove_task(subtasks[filename])
                                    del subtasks[filename]
                        return progress_handler

                    final_stats = await downloader.download_media_from_room(
                        room_id, make_callback(task, room_name, only_oldest), only_oldest=only_oldest
                    )
                    progress.update(
                        task, description=f"[bold green]Done: {room_name}[/bold green]"
                    )
                    
                    if final_stats and not only_oldest:
                        console.print(f"  [bold magenta]↳ {room_name} Summary:[/bold magenta]")
                        console.print(f"      [green]Downloaded:[/green] {final_stats.get('downloaded', 0)} files ({_format_size(final_stats.get('downloaded_size', 0))})")
                        console.print(f"      [blue]Already Existed:[/blue] {final_stats.get('existed', 0)} files ({_format_size(final_stats.get('existed_size', 0))})")
                        console.print(f"      [yellow]Dupes Skipped:[/yellow] {final_stats.get('deduped', 0)} files ({_format_size(final_stats.get('deduped_size', 0))})")
                        console.print(f"      [red]Errors:[/red] {final_stats.get('errors', 0)} files ({_format_size(final_stats.get('errors_size', 0))})")
                        console.print()

            console.print("[bold green]Backup complete![/bold green]")
            try:
                import winsound
                # Single, shorter beep (lower pitch) to be less abrasive
                winsound.Beep(600, 200)
            except Exception:
                pass
                
            console.print("\n[bold cyan]Returning to room selection...[/bold cyan]\n")
    finally:
        await client_wrapper.close()


if __name__ == "__main__":
    asyncio.run(main())
