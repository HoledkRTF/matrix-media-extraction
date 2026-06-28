from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

console = Console()

import os

def get_credentials():
    console.print("[bold blue]Matrix Media Backup[/bold blue]")
    
    homeserver = os.getenv("MATRIX_HOMESERVER", "")
    username = os.getenv("MATRIX_USERNAME", "")
    password = os.getenv("MATRIX_PASSWORD", "")
    
    if not password:
        password = Prompt.ask(f"Password for {username} on {homeserver}", password=True)
        
    return homeserver, username, password

def _format_size(size_bytes):
    """Format bytes into a human-readable string."""
    if size_bytes is None:
        return "—"
    if size_bytes == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"

def select_rooms(rooms_dict, room_statuses=None):
    if room_statuses is None:
        room_statuses = {}
        
    table = Table(title="Available Rooms")
    table.add_column("ID", justify="right", style="cyan", no_wrap=True)
    table.add_column("Room Name", style="magenta")
    table.add_column("🔒", justify="center")
    table.add_column("Media", justify="right", style="yellow")
    table.add_column("Total Size", justify="right", style="blue")
    table.add_column("Status", justify="center", style="green")
    
    room_list = list(rooms_dict.items())
    # rooms_dict values are now (name, is_encrypted) tuples
    # Sort alphabetically by room name, case-insensitive
    room_list.sort(key=lambda x: (x[1][0] if isinstance(x[1], tuple) else x[1] or x[0]).lower())
    
    total_unencrypted_size = 0
    total_encrypted_size = 0
    total_unencrypted_media = 0
    total_encrypted_media = 0
    
    for idx, (room_id, room_info) in enumerate(room_list):
        if isinstance(room_info, tuple):
            room_name, is_encrypted = room_info
        else:
            room_name, is_encrypted = room_info, False
        
        # status tuple is (media_count, is_cached, total_size_bytes)
        media_count, is_cached, total_size = room_statuses.get(room_id, (0, False, 0))
        
        status_text = ""
        if is_cached:
            status_text = "[✓]"
        
        encrypted_text = "[red]🔒[/red]" if is_encrypted else ""
        media_text = str(media_count) if media_count is not None else "?"
        size_text = _format_size(total_size)
        
        # Track totals
        if is_encrypted:
            total_encrypted_size += total_size
            total_encrypted_media += (media_count or 0)
        else:
            total_unencrypted_size += total_size
            total_unencrypted_media += (media_count or 0)
            
        table.add_row(str(idx + 1), room_name, encrypted_text, media_text, size_text, status_text)
        
    console.print(table)
    console.print(f"  [bold]Unencrypted:[/bold] [yellow]{total_unencrypted_media}[/yellow] files, [blue]{_format_size(total_unencrypted_size)}[/blue]  |  [bold]Encrypted:[/bold] [yellow]{total_encrypted_media}[/yellow] files, [blue]{_format_size(total_encrypted_size)}[/blue]  |  [bold]Total:[/bold] [yellow]{total_unencrypted_media + total_encrypted_media}[/yellow] files, [blue]{_format_size(total_unencrypted_size + total_encrypted_size)}[/blue]")
    console.print()
    
    choices = Prompt.ask("Select rooms (comma separated, e.g. 1,3,5-10, or 'all'. Append 'x' for oldest only, e.g. 121x, allx)")
    
    selected_rooms = {} # Dict of room_id: only_oldest

    if choices.strip().lower() == 'all':
        for r in room_list:
            selected_rooms[r[0]] = False
        return selected_rooms
    if choices.strip().lower() in ('allx', '0x'):
        for r in room_list:
            selected_rooms[r[0]] = True
        return selected_rooms
        
    parts = choices.split(',')
    for part in parts:
        part = part.strip().lower()
        only_oldest = False
        if 'x' in part:
            only_oldest = True
            part = part.replace('x', '')
            
        if '-' in part:
            try:
                start_str, end_str = part.split('-')
                start, end = int(start_str), int(end_str)
                for i in range(start, end + 1):
                    if 1 <= i <= len(room_list):
                        selected_rooms[room_list[i-1][0]] = only_oldest
            except (ValueError, IndexError):
                pass
        else:
            try:
                i = int(part)
                if i == 0:
                    for r in room_list:
                        selected_rooms[r[0]] = only_oldest
                elif 1 <= i <= len(room_list):
                    selected_rooms[room_list[i-1][0]] = only_oldest
            except (ValueError, IndexError):
                pass
                
    return selected_rooms
