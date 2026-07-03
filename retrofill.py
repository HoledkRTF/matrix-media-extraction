import os
import asyncio
from cli import console, Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

async def _transcode_video(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".mp4":
        return
        
    out_path = os.path.splitext(filepath)[0] + ".mp4"
    cmd = [
        "ffmpeg", "-y", "-i", filepath,
        "-c:v", "h264_nvenc", "-crf", "18", "-preset", "slow",
        "-c:a", "aac", "-b:a", "192k",
        out_path
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        if proc.returncode == 0 and os.path.exists(out_path):
            stat = os.stat(filepath)
            os.remove(filepath)
            os.utime(out_path, (stat.st_atime, stat.st_mtime))
    except Exception:
        pass

async def process_backlog(downloads_dir="downloads"):
    console.print("\n[bold cyan]Starting Retroactive Media Processing...[/bold cyan]")
    
    # Collect files
    files_to_process = []
    if os.path.exists(downloads_dir):
        for root, dirs, files in os.walk(downloads_dir):
            room_name = os.path.basename(root)
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                filepath = os.path.join(root, f)
                if ext in (".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm"):
                    files_to_process.append(("video", filepath, room_name, f))
                
    if not files_to_process:
        console.print("[green]No unprocessed videos found in backlog![/green]")
        return
        
    console.print(f"[cyan]Found {len(files_to_process)} heavy video files to transcode.[/cyan]")
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("[green]{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Transcoding Videos...", total=len(files_to_process))
        
        video_sem = asyncio.Semaphore(2) # NVENC Encoders
        
        async def process_file(ptype, filepath, room_name, fname):
            try:
                if ptype == "video":
                    async with video_sem:
                        await _transcode_video(filepath)
            except Exception as e:
                pass
            progress.advance(task)
                
        tasks = [process_file(pt, fp, rn, fn) for pt, fp, rn, fn in files_to_process]
        await asyncio.gather(*tasks)
            
    console.print("[bold green]Retroactive processing complete![/bold green]")

