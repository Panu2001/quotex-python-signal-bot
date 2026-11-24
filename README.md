<div align="center">
<img width="1200" height="475" alt="GHBanner" src="https://ai.google.dev/static/site-assets/images/share-ais-513315318.png" />
</div>

# Run and deploy your AI Studio app

This contains everything you need to run your app locally.

View your app in AI Studio: https://ai.studio/apps/dc299713-bbc4-486a-8b72-2adbdd4d239f

## Run Locally

**Prerequisites:**  Node.js


1. Install dependencies:
   `npm install`
2. Run the app:
   `npm run dev`
# Stubay Ai data and recovery

Stubay Ai keeps candles, screenshots, decisions, and paper-trade outcomes in the Paper Lab SQLite database. Restarting Stubay Ai or SignalDesk reopens the same database. The **Restart AI** button schedules a backup and a new scan, when Quotex is connected.

The worker creates hourly backups while running once scan history exists. On Windows these are stored in `%LOCALAPPDATA%\SignalDesk\Stubay Backups` outside the source and preview folders. Automatic backups do not delete older copies. Backups include SQLite history and model artifacts. API keys and bot tokens are stored separately.

To recover a trusted local archive, stop the instance you intend to recover and restore to a new empty folder:

```powershell
Set-Location python_bot
python -c "from paper_lab.storage import PaperLabStore; import sys; print(PaperLabStore.restore_backup(sys.argv[1], sys.argv[2]))" "C:\Path\backup.tar.gz" "C:\Path\Stubay-Recovered"
```

After verifying the recovered copy, point the recovering instance's `data_dir` setting at the new folder. Restore refuses to overwrite an existing data folder.
