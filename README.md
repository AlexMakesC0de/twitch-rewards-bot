# Twitch Drops Bot

Never miss another Twitch Drop! This bot automatically watches streams and earns drop rewards for your favorite games — all from a simple dashboard you control with your mouse.

## What Does It Do?

- **Finds drops** for the games you pick
- **Watches streams** in the background so you earn progress
- **Claims rewards** automatically when they're ready
- **Tracks everything** - see your progress, history, and active campaigns at a glance
- **Notifies you** via Discord or Email (optional)

## Getting Started

### 1. Install Python

Download and install Python from [python.org](https://www.python.org/downloads/). **During installation, make sure to check the box that says "Add Python to PATH".**

### 2. Download This Bot

Download this project as a ZIP file and extract it to a folder on your computer, or clone with Git if you know how.

### 3. Run It

Double-click the **start.bat** file in the project folder. That's it!

- The first time you run it, it will automatically install everything it needs.
- Your browser will open to the dashboard.

### 4. Log In

In the dashboard, go to **Settings** and click **Login with Twitch**. You'll get a code — enter it at the Twitch link shown on screen. Once you approve it, you're connected.

### 5. Add Games

Go to the **Games** page and search for any game you want to track drops for. Click a game to add it.

### 6. Let It Run

That's all! The bot will automatically find active drop campaigns, watch streams, and claim rewards. You can check the **Dashboard** to see what's happening, or just leave it running in the background.

## Dashboard Pages

| Page | What It Shows |
|------|---------------|
| **Dashboard** | Overview stats, drop progress, activity log |
| **Games** | Add or remove games to track. Drag to set priority |
| **Drops** | Active campaigns and your inventory |
| **Watch** | Live watch sessions and available campaigns |
| **History** | Past drops you've earned |
| **Settings** | Login, theme, sound, and notification setup |

## Notifications (Optional)

### Discord

1. In your Discord server: **Server Settings → Integrations → Webhooks**
2. Create a webhook and copy its URL
3. Paste it in **Settings → Discord Notifications** in the dashboard

### Email

1. Fill in your SMTP details in **Settings → Email Notifications**
2. For Gmail: use an [App Password](https://myaccount.google.com/apppasswords) (not your regular password)

## Tips

- **First time?** The bot will offer you a quick tour of the dashboard when you first open it.
- **Dark/Light mode** - click the moon/sun icon in the sidebar.
- **Drag games** to set which ones get watched first.
- **Sound alert** - enable it in Settings to hear a chime when a drop is claimed.

## Disclaimer

This bot interacts with Twitch's platform programmatically. Automated viewing may be against Twitch's Terms of Service. **Use at your own risk.** The authors are not responsible for any account actions taken by Twitch.

For educational and personal use only.

## License

MIT License — see [LICENSE](LICENSE) for details.
