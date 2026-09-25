# Motion Photo Extractor

Your Pixel phone hides a short video inside its photos. This pulls them out.

```
cd Photos
motionextract
```

Point it at a folder, get a folder of MP4s. Your original photos are never touched.

**No dependencies. Pure Python. Windows, Mac and Linux.**

---

## What is a motion photo?

Pixel phones shoot *motion photos* — a normal-looking JPEG with a 1–3 second MP4 clip silently tucked onto the end of the file. Every app treats it as an ordinary photo, so the video is invisible and effectively stuck in there.

This tool finds that clip and saves it as a real MP4 file.

---

# Setup

Three steps, once. Then you just type `motionextract` whenever you need it.

## Step 1 — Install Python

Skip this if you already have Python 3.8 or newer.

**Windows:** download from [python.org/downloads](https://www.python.org/downloads/) and run the installer.

> ⚠️ **On the first screen of the installer, tick "Add python.exe to PATH".**
> It's a small checkbox at the bottom and it's easy to miss. If you skip it, Windows won't find Python afterwards. If you've already installed without it, just run the installer again and choose *Modify*.

**Mac:** you already have Python, but it may be old. Check with `python3 --version`. If it's below 3.8, install a current version from [python.org/downloads](https://www.python.org/downloads/).

**Linux:** `sudo apt install python3 python3-pip` (or your distro's equivalent).

### Check it worked

Open a **new** terminal window — PowerShell on Windows, Terminal on Mac — and run:

```powershell
py --version
```

```bash
python3 --version
```

You should see something like `Python 3.12.1`. If instead you get "not recognized" or "command not found", see [Troubleshooting](#troubleshooting) below.

> **A note on the commands in this guide.** Windows examples use `py`, Mac and Linux examples use `python3`. Use whichever matches your machine — they do the same thing.

## Step 2 — Install the tool

One command. Copy it exactly.

**Windows:**

```powershell
py -m pip install https://github.com/HeadlessClaudesmann/MotionPhotoExtractor/archive/refs/heads/master.zip
```

**Mac / Linux:**

```bash
python3 -m pip install https://github.com/HeadlessClaudesmann/MotionPhotoExtractor/archive/refs/heads/master.zip
```

You'll see a few lines of output ending in `Successfully installed motionextract`.

> **Why a .zip link and not `git clone`?** So you don't need Git installed. This downloads the code directly through Python.

### Check it worked

```
motionextract --version
```

You should see `motionextract 2.1.0` (or higher).

If you get "not recognized" on Windows, don't worry — the tool installed fine,
Windows just can't see it yet. This always works instead:

```powershell
py -m motionextract --version
```

You can use `py -m motionextract` everywhere this guide says `motionextract`, or
see [Troubleshooting](#motionextract-is-not-recognized-windows) to get the short
command working.

## Step 3 — Run it

Navigate to your photos, then run the command.

**Windows:**

```powershell
cd "$env:USERPROFILE\Pictures\Pixel"
motionextract
```

**Mac / Linux:**

```bash
cd ~/Pictures/Pixel
motionextract
```

That's it. Videos land in a new `extracted_videos` folder right there.

> **Tip for Windows:** you can skip the typing. Open the photo folder in File Explorer, then in the address bar at the top type `powershell` and press Enter. A terminal opens already in that folder — just type `motionextract`.

---

# Using it

## What a run looks like

```
> motionextract
[>>] 47 JPEG(s) found in C:\Users\dave\Pictures\Pixel
[>>] output: C:\Users\dave\Pictures\Pixel\extracted_videos

  [OK] PXL_20251130_140645906.MP.jpg  ->  PXL_20251130_140645906.MP_video.mp4  (2.5 MB)
  [OK] PXL_20251203_184331147.MP.jpg  ->  PXL_20251203_184331147.MP_video.mp4  (1.8 MB)
  [OK] PXL_20251204_173217796.MP.jpg  ->  PXL_20251204_173217796.MP_video.mp4  (3.1 MB)
  [--] IMG_20240104_093312.jpg

[done] 3 video(s) extracted, 44 non-motion JPEG(s) skipped
```

Reading the prefixes:

| | |
|---|---|
| `[OK]` | Video extracted |
| `[--]` | Ordinary photo, no video inside — nothing to do |
| `[!!]` | Something went wrong with that one file. The run keeps going |
| `[>>]` | General information |

Most of your photos will be `[--]`, and that's normal. Only shots taken with motion photo enabled have a clip inside.

## Common commands

Run these from inside your photo folder.

| What you want | Command |
|---|---|
| Extract from this folder | `motionextract` |
| Include subfolders too | `motionextract -r` |
| Preview without changing anything | `motionextract --dry-run` |
| A different folder | `motionextract "C:\Users\dave\Pictures"` |
| Just one photo | `motionextract photo.jpg` |
| Save videos somewhere else | `motionextract -o "D:\Videos"` |
| Save videos next to the photos | `motionextract -o same` |
| Full list of options | `motionextract --help` |

**Not sure what it'll do?** Run `motionextract --dry-run` first. It reports exactly what it would extract and writes nothing at all.

> Wrap paths in quotes if they contain spaces.

## Good to know

- **Your photos are never modified.** The tool only reads them.
- **Running it twice is safe.** It won't overwrite existing videos — it adds `_2`, `_3` and so on.
- **Only `.jpg` and `.jpeg` files are touched.** Everything else in the folder is left alone.
- **Quality is untouched.** The clip is copied out exactly as your phone recorded it. No re-encoding.

---

# Troubleshooting

### "py is not recognized" / "python3: command not found"

Python isn't installed, or wasn't added to PATH during install.

Re-run the python.org installer, choose **Modify**, and make sure **"Add python.exe to PATH"** is ticked. Then **close and reopen your terminal** — PATH changes only apply to new windows.

### "motionextract is not recognized" (Windows)

This is the most common hiccup, and it's cosmetic — Python and the tool are
almost certainly fine. Work through it in order.

**First, check it's actually installed:**

```powershell
py -m pip show motionextract
```

If that says `WARNING: Package(s) not found`, the install didn't happen. Go back
to [Step 2](#step-2--install-the-tool) and watch the output for errors.

**If it is installed, this works right now, regardless of PATH:**

```powershell
py -m motionextract
```

All the same options work — `py -m motionextract -r`, `py -m motionextract --dry-run`,
and so on. If you're happy typing that, you're done; everything below is optional.

**To get the short `motionextract` command working**, its folder needs to be on
your PATH. The `pip show` output above tells you where it is — look at the
`Location:` line:

```
Location: C:\Users\you\AppData\Roaming\Python\Python314\site-packages
```

Swap `site-packages` for `Scripts` and that's your folder. Confirm it:

```powershell
dir "C:\Users\you\AppData\Roaming\Python\Python314\Scripts\motionextract.exe"
```

Now add that folder to your PATH. **Via the GUI**, which is the safer route:

1. Press Start, type `environment`, choose **"Edit environment variables for your account"**
2. Select **Path** in the top box, then **Edit** → **New**
3. Paste the folder path
4. **OK** on every dialog

Or **via PowerShell** — this version won't create a duplicate if you run it twice:

```powershell
$dir = "C:\paste\your\Scripts\folder\here"
$old = [Environment]::GetEnvironmentVariable('PATH','User')
if ($old -split ';' -notcontains $dir) {
  $new = if ([string]::IsNullOrEmpty($old)) { $dir } else { "$old;$dir" }
  [Environment]::SetEnvironmentVariable('PATH', $new, 'User')
  "Added - restart PowerShell"
} else { "Already there" }
```

> If your user PATH contains entries written with variables like
> `%USERPROFILE%\...`, the PowerShell route permanently expands them into literal
> paths. It's harmless in practice, but the GUI route avoids it entirely.

**Close and reopen PowerShell** — PATH changes only apply to new windows. Then
`motionextract --version` should work.

> pip also prints a yellow `WARNING: The script motionextract.exe is installed in
> '...' which is not on PATH` line during Step 2, naming the exact folder. If that
> output is still on screen, you can take the path straight from there.

**Alternative** — `pipx` manages PATH for you and keeps the tool isolated:

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
```

Close and reopen your terminal, then:

```powershell
pipx install https://github.com/HeadlessClaudesmann/MotionPhotoExtractor/archive/refs/heads/master.zip
```

### "TypeError: unsupported operand type(s) for |"

You're on an old copy of the tool. Version 2.0.0 fixed this. Reinstall with the Step 2 command, adding `--upgrade`:

```powershell
py -m pip install --upgrade https://github.com/HeadlessClaudesmann/MotionPhotoExtractor/archive/refs/heads/master.zip
```

Confirm with `motionextract --version` — you want `2.0.0` or higher.

### "No JPEG files found"

You're not in the folder you think you are. Run `dir` (Windows) or `ls` (Mac/Linux) to see where you are. Also note the tool doesn't look inside subfolders unless you add `-r`.

### Everything shows `[--]`

None of those photos have a video inside. This is normal for photos not shot in motion photo mode, screenshots, downloaded images, or anything edited and re-saved by another app (editing usually strips the clip).

### "Permission denied" or "Access is denied"

The folder is protected, or the files are open in another program. Try a folder inside your own user directory, and close any photo app that might be holding the files.

### Still stuck?

Open an issue at [github.com/HeadlessClaudesmann/MotionPhotoExtractor/issues](https://github.com/HeadlessClaudesmann/MotionPhotoExtractor/issues) with the command you ran and the full output.

---

# Reference

## Output filenames

Videos are named after the photo they came from:

```
PXL_20251130_140645906.MP.jpg  →  PXL_20251130_140645906.MP_video.mp4
```

Run it again and existing files are preserved, not overwritten:

```
PXL_20251130_140645906.MP_video.mp4
PXL_20251130_140645906.MP_video_2.mp4
```

## Exit codes

For anyone scripting against it.

| Code | Meaning |
|---|---|
| `0` | Ran fine, including "nothing to do" |
| `1` | Bad input — path not found, or not a JPEG |
| `2` | Ran, but one or more files failed |

## Format support

Detected automatically — you don't need to know which kind you have.

| Format | How it works |
|--------|-------------|
| **New** (Pixel 6+) | Reads `GCamera:MotionPhoto` + `Container:Directory` from XMP metadata |
| **Old** (earlier Pixels) | Reads the `MicroVideoOffset` attribute from XMP metadata |
| **Fallback** | Scans backward for MP4 box markers (`ftyp` / `moov` / `mdat`) |

`.trashed-` prefixed files, which Android creates for deleted photos copied over USB, extract normally.

## The GUI

There's also a windowed version with folder pickers, a live progress log, and a "play them all in VLC" button:

```
motionextract-gui
```

It shares the same extraction engine. The command line version is the better-tested of the two.

## Running without installing

If you'd rather not install anything, download the repo as a ZIP from GitHub, extract it, open a terminal in that folder and run:

```powershell
py -m motionextract "C:\path\to\photos"
```

```bash
python3 -m motionextract ~/path/to/photos
```

## Uninstalling

```
py -m pip uninstall motionextract
```

---

## Notes for the curious

The extracted MP4 is the raw embedded clip, copied byte for byte — no re-encoding, so no quality loss. It's H.264, which is how the Pixel records it. Producing a genuinely "uncompressed" video would require ffmpeg to transcode, and would balloon a 2 MB clip into hundreds of megabytes.

## Licence

MIT
