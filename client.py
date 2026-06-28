import asyncio
from nio import AsyncClient, LoginResponse, RoomMessageText, RoomMessageMedia, SyncResponse
import os

class MatrixBackupClient:
    def __init__(self, homeserver, username, password, store_path="./store"):
        self.homeserver = homeserver
        self.username = username
        self.password = password
        self.store_path = store_path
        
        # We need a store path to keep E2EE keys
        if not os.path.exists(store_path):
            os.makedirs(store_path)
            
        self.client = AsyncClient(
            self.homeserver,
            self.username,
            store_path=self.store_path
        )
        
    async def login(self):
        response = await self.client.login(self.password)
        if isinstance(response, LoginResponse):
            return True, "Login successful"
        return False, getattr(response, "message", "Unknown error")
        
    async def sync(self):
        # Do an initial sync to get rooms
        sync_resp = await self.client.sync(timeout=30000, full_state=True)
        return isinstance(sync_resp, SyncResponse)

    async def get_rooms(self):
        # Return dict of room_id: (room_name, is_encrypted)
        rooms = {}
        for room_id, room in self.client.rooms.items():
            name = room.display_name or room_id
            is_encrypted = getattr(room, "encrypted", False)
            rooms[room_id] = (name, is_encrypted)
        return rooms
        
    async def close(self):
        await self.client.close()
