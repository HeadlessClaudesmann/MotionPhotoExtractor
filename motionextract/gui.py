"""
Tkinter GUI. Standard library only.

Extraction logic lives in core.py, shared with the CLI.
Launch with `motionextract-gui`, or `python -m motionextract.gui`.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk

from .core import (
    JPEG_EXTENSIONS,
    ExtractionError,
    claim_output_path,
    collect_jpegs,
    extract_to,
)

DEFAULT_OUTPUT_DIRNAME = 'extracted_videos'


# ---------------------------------------------------------------------------
# Player helpers
# ---------------------------------------------------------------------------

def _find_vlc() -> str | None:
    if sys.platform == 'darwin':
        path = Path('/Applications/VLC.app/Contents/MacOS/VLC')
        return str(path) if path.exists() else None
    if sys.platform == 'win32':
        bases = [os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMFILES(X86)')]
        for base in filter(None, bases):
            path = Path(base) / 'VideoLAN' / 'VLC' / 'vlc.exe'
            if path.exists():
                return str(path)
        return None
    return 'vlc'  # Linux: assume it is on PATH


def _open_default(path: Path) -> None:
    if sys.platform == 'darwin':
        subprocess.Popen(['open', str(path)])
    elif sys.platform == 'win32':
        os.startfile(str(path))  # noqa: S606 - Windows-only API
    else:
        subprocess.Popen(['xdg-open', str(path)])


def _open_playlist(paths: list[Path]) -> None:
    if not paths:
        return
    playlist = paths[0].parent / 'extracted_videos.m3u'
    playlist.write_text(
        '#EXTM3U\n' + '\n'.join(str(p) for p in paths) + '\n',
        encoding='utf-8',
    )
    vlc = _find_vlc()
    subprocess.Popen([vlc, str(playlist)]) if vlc else _open_default(playlist)


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _worker(source: Path, output_dir: Path, recursive: bool, log_q: queue.Queue) -> None:
    """
    Runs extraction off the main thread, reporting (tag, payload) via log_q.
    Tags: ok, skip, warn, info, done. The 'done' payload is list[Path].
    """
    if source.is_file():
        jpegs = [source] if source.suffix.lower() in JPEG_EXTENSIONS else []
        if not jpegs:
            log_q.put(('warn', f'{source.name} is not a JPEG.'))
            log_q.put(('done', []))
            return
    else:
        jpegs = collect_jpegs(source, recursive)

    if not jpegs:
        log_q.put(('warn', 'No JPEG files found in the selected folder.'))
        log_q.put(('done', []))
        return

    log_q.put(('info', f'{len(jpegs)} JPEG(s) found\n'))

    extracted: list[Path] = []
    # How many photos of each stem we have seen, so that two photos sharing a
    # filename in a recursive run are told apart from work already finished.
    seen: dict[str, int] = {}
    skipped = already = failed = 0

    for jpeg in jpegs:
        try:
            dest, already_done = claim_output_path(output_dir, jpeg.stem, seen)

            if already_done:
                already += 1
                # Already on disk, so still worth offering in the playlist.
                extracted.append(dest)
                log_q.put(('skip', f'[==]  {jpeg.name}  ->  {dest.name} already exists'))
                continue

            out = extract_to(jpeg, dest)
            if out is None:
                skipped += 1
                log_q.put(('skip', f'[--]  {jpeg.name}'))
            else:
                extracted.append(out)
                size_mb = out.stat().st_size / 1_048_576
                log_q.put(('ok', f'[OK]  {jpeg.name}  ->  {out.name}  ({size_mb:.1f} MB)'))
        except ExtractionError as exc:
            failed += 1
            log_q.put(('warn', f'[!!]  {jpeg.name}: {exc}'))
        except OSError as exc:
            failed += 1
            log_q.put(('warn', f'[!!]  {jpeg.name}: {exc.strerror or exc}'))

    tail = f'\nDone.  {len(extracted) - already} extracted   {skipped} skipped'
    if already:
        tail += f'   {already} already done'
    if failed:
        tail += f'   {failed} failed'
    log_q.put(('info', tail))
    log_q.put(('done', extracted))


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title('Motion Photo Extractor')
        self.minsize(640, 480)
        self._log_q: queue.Queue = queue.Queue()
        self._extracted: list[Path] = []
        self._build_ui()
        self._poll()

    def _build_ui(self) -> None:
        pad = dict(padx=12, pady=6)

        src_frame = ttk.LabelFrame(self, text='Source folder')
        src_frame.pack(fill='x', **pad)
        self._src_var = tk.StringVar()
        ttk.Entry(src_frame, textvariable=self._src_var).pack(
            side='left', fill='x', expand=True, padx=(6, 2), pady=6)
        ttk.Button(src_frame, text='Browse...', command=self._browse_src).pack(
            side='left', padx=(0, 6), pady=6)

        out_frame = ttk.LabelFrame(self, text='Output folder')
        out_frame.pack(fill='x', **pad)
        self._out_var = tk.StringVar()
        ttk.Entry(out_frame, textvariable=self._out_var).pack(
            side='left', fill='x', expand=True, padx=(6, 2), pady=6)
        ttk.Button(out_frame, text='Browse...', command=self._browse_out).pack(
            side='left', padx=(0, 6), pady=6)

        ctrl_frame = ttk.Frame(self)
        ctrl_frame.pack(fill='x', padx=12, pady=(0, 6))
        self._recursive_var = tk.BooleanVar()
        ttk.Checkbutton(
            ctrl_frame, text='Include subfolders', variable=self._recursive_var,
        ).pack(side='left')
        self._extract_btn = ttk.Button(
            ctrl_frame, text='Extract', command=self._start_extraction)
        self._extract_btn.pack(side='right')

        log_frame = ttk.LabelFrame(self, text='Progress')
        log_frame.pack(fill='both', expand=True, **pad)
        self._log = scrolledtext.ScrolledText(
            log_frame, state='disabled', height=16,
            font=('Courier', 11), wrap='none')
        self._log.pack(fill='both', expand=True, padx=6, pady=6)
        self._log.tag_config('ok', foreground='#2a9d2a')
        self._log.tag_config('skip', foreground='#888888')
        self._log.tag_config('warn', foreground='#cc5500')
        self._log.tag_config('info', foreground='#000000')

        action_frame = ttk.Frame(self)
        action_frame.pack(fill='x', padx=12, pady=(0, 12))
        self._vlc_btn = ttk.Button(
            action_frame, text='Open playlist in VLC',
            command=lambda: _open_playlist(self._extracted), state='disabled')
        self._vlc_btn.pack(side='left', padx=(0, 6))
        self._preview_btn = ttk.Button(
            action_frame, text='Preview last video',
            command=self._preview_last, state='disabled')
        self._preview_btn.pack(side='left')

    def _browse_src(self) -> None:
        chosen = filedialog.askdirectory(title='Select source folder')
        if chosen:
            self._src_var.set(chosen)
            if not self._out_var.get():
                self._out_var.set(str(Path(chosen) / DEFAULT_OUTPUT_DIRNAME))

    def _browse_out(self) -> None:
        chosen = filedialog.askdirectory(title='Select output folder')
        if chosen:
            self._out_var.set(chosen)

    def _start_extraction(self) -> None:
        raw = self._src_var.get().strip()
        if not raw:
            self._log_line('warn', 'Please select a source folder first.')
            return

        source = Path(raw).expanduser().resolve()
        if not source.exists():
            self._log_line('warn', f'Path not found: {source}')
            return

        out_raw = self._out_var.get().strip()
        if out_raw:
            output_dir = Path(out_raw).expanduser().resolve()
        else:
            base = source if source.is_dir() else source.parent
            output_dir = base / DEFAULT_OUTPUT_DIRNAME
            self._out_var.set(str(output_dir))

        self._clear_log()
        self._extracted = []
        for btn in (self._extract_btn, self._vlc_btn, self._preview_btn):
            btn.config(state='disabled')

        threading.Thread(
            target=_worker,
            args=(source, output_dir, self._recursive_var.get(), self._log_q),
            daemon=True,
        ).start()

    def _preview_last(self) -> None:
        if self._extracted:
            _open_default(self._extracted[-1])

    def _log_line(self, tag: str, text: str) -> None:
        self._log.config(state='normal')
        self._log.insert('end', text + '\n', tag)
        self._log.see('end')
        self._log.config(state='disabled')

    def _clear_log(self) -> None:
        self._log.config(state='normal')
        self._log.delete('1.0', 'end')
        self._log.config(state='disabled')

    def _poll(self) -> None:
        """Drain the worker queue on the main thread every 50 ms."""
        try:
            while True:
                tag, payload = self._log_q.get_nowait()
                if tag == 'done':
                    self._extracted = payload
                    self._extract_btn.config(state='normal')
                    if payload:
                        self._vlc_btn.config(state='normal')
                        self._preview_btn.config(state='normal')
                else:
                    self._log_line(tag, payload)
        except queue.Empty:
            pass
        self.after(50, self._poll)


def main() -> int:
    App().mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
