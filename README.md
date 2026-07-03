# Matrix Media Extraction

A tool to extract and download all media from Matrix chat rooms, including E2E encrypted rooms. It scans room histories backwards, downloading images, videos, and files, and properly deduplicates them so you don't end up with massive storage waste from identical forwarded files.

## Shit it do

- Downloads media from both unencrypted and encrypted(OML i hate matrix so much) Matrix rooms.
- Prevents storage bloat by hashing files and skipping exact duplicates(i dont have space, ts too expensive).
- Resumes incomplete scans using a local cache(maybe kinda useless).
- Command-line interface with progress bars and detailed room statistics(storage).

## Setup Instructions(AI my goat)

1. **Install Python**: Make sure you have Python 3 installed.
2. **Clone the repository**: Download this code to your machine.
3. **Set up a virtual environment** (recommended):
   ```bash
   python -m venv venv
   .\venv\Scripts\activate  # Windows
   source venv/bin/activate # Linux/Mac
   ```
4. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
5. **Configure your credentials**:
   Open the `.env` file and enter your Matrix homeserver, username, and password. 

   To decrypt encrypted rooms, you must export your E2E room keys from the Element client (Settings -> Security & Privacy -> Export E2E room keys). Provide the path to this export file and the passphrase in the `.env` file. If you leave the path blank, the script will attempt to auto-detect a file named `element-keys*.txt` in your Downloads folder.

6. **Run the tool**:
   ```bash
   python main.py
   ```
   Select the rooms you want to scan and wait for the downloads to finish. The files will be saved in organized folders.

## Future Plans

I plan to expand this tool to include entire room backups—not just media, but also text messages, reactions, and threaded replies. But it was just extremely storage intensive plus my pc was waiting half the time just for the matrix servers to respond to me in the first place, i genuienly cant believe how unoptimized matrix servers are, it makes sense why the servers im a part of decided to stop hosting them ngl, if i had the hardware to host a large server it still might not be worth the effort. Using element i always saw the 
