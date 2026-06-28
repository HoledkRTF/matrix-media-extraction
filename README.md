# Matrix Media Extraction

A tool to extract and download all media from Matrix chat rooms, including E2E encrypted rooms. It scans room histories backwards, downloading images, videos, and files, and properly deduplicates them so you don't end up with massive storage waste from identical forwarded files.

## Features

- Downloads media from both encrypted and unencrypted Matrix rooms.
- Prevents storage bloat by hashing files and skipping exact duplicates.
- Resumes incomplete scans using a local cache.
- Command-line interface with progress bars and detailed room statistics.

## Setup Instructions

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

Full room backups are missing from this release. Polling the Matrix `/messages` API to pull text, threaded replies, & JSON reaction events balloons a 50 MB media backup into a 2 GB database file that takes 45 minutes to parse. I am restricting this tool to media extraction until I can write a parser that doesn't waste disk space on unneeded sync tokens.
