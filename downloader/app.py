import logging, threading, socket, os, io, zipfile
import yt_dlp
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from flask import Flask, jsonify, request, render_template, send_file, send_from_directory
import qrcode
from PIL import Image, ImageTk
import requests

# -------- App setup --------
SAVE_FOLDER = os.path.join(os.getcwd(), "Downloads")
os.makedirs(SAVE_FOLDER, exist_ok=True)

app = Flask(__name__, template_folder="templates")
log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

# Progress state shared to frontend
progress_data = {
    "size": 0,
    "downloaded": 0,
    "speed": 0,
    "eta": 0,
    "playlist_count": 0,   # total videos in playlist
    "playlist_index": 0    # current video index being downloaded (1-based)
}


# -------- Flask routes --------
@app.route("/")
def home():
    return render_template("index.html", savepath=SAVE_FOLDER)

def hook(d):
    status = d.get("status")
    if status == "downloading":
        progress_data["size"] = d.get("total_bytes", 0) or d.get("total_bytes_estimate", 0)
        progress_data["downloaded"] = d.get("downloaded_bytes", 0)
        progress_data["speed"] = d.get("speed", 0)
        progress_data["eta"] = d.get("eta", 0)
        # playlist info (if available)
        if "playlist_index" in d:
            # yt-dlp uses 1-based playlist_index
            progress_data["playlist_index"] = d.get("playlist_index") or progress_data["playlist_index"]
        if "playlist_count" in d:
            progress_data["playlist_count"] = d.get("playlist_count") or progress_data["playlist_count"]
    elif status == "finished":
        progress_data["downloaded"] = progress_data["size"]

@app.route("/start", methods=["POST"])
def start_download():
    raw = request.form.get("url", "").strip()
    urls = raw.split()
    quality = request.form.get("quality", "720")
    fmt = request.form.get("format", "mp4")
    auto_download = request.form.get("auto")
    playlist_mode = request.form.get("playlist")  # when set, allow full playlist

    if not urls:
        return jsonify({"error": "YouTube URL(s) required"}), 400

    results = []
    for url in urls:
        if fmt == "mp4":
            ydl_opts = {
                "progress_hooks": [hook],
                "outtmpl": os.path.join(SAVE_FOLDER, "%(title)s.%(ext)s"),
                "format": f"bestvideo[height={quality}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "merge_output_format": "mp4",
                "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
                "noplaylist": not playlist_mode,
            }
        else:
            ydl_opts = {
                "progress_hooks": [hook],
                "outtmpl": os.path.join(SAVE_FOLDER, "%(title)s.%(ext)s"),
                "format": "bestaudio/best",
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}],
                "noplaylist": not playlist_mode,
            }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # When playlist, info contains "entries"
                if isinstance(info, dict) and "entries" in info and info["entries"]:
                    progress_data["playlist_count"] = len(info["entries"])
                    # Collect expected final file names
                    for entry in info["entries"]:
                        title = entry.get("title", "video")
                        results.append(os.path.join(SAVE_FOLDER, f"{title}.{fmt}"))
                else:
                    title = info.get("title", "video")
                    results.append(os.path.join(SAVE_FOLDER, f"{title}.{fmt}"))
        except Exception as e:
            results.append(f"Error for {url}: {e}")

    if auto_download and len(results) == 1 and os.path.exists(results[0]):
        return send_file(results[0], as_attachment=True)
    return jsonify({"status": "success", "files": results})

@app.route("/progress")
def progress():
    return jsonify(progress_data)

@app.route("/files")
def list_files():
    files = [f for f in os.listdir(SAVE_FOLDER) if f.lower().endswith((".mp3", ".mp4"))]
    return render_template("files.html", files=files, savepath=SAVE_FOLDER)

@app.route("/download/<path:filename>")
def download_file(filename):
    return send_from_directory(SAVE_FOLDER, filename, as_attachment=True)

@app.route("/download_all")
def download_all():
    files = [f for f in os.listdir(SAVE_FOLDER) if f.lower().endswith((".mp3", ".mp4"))]
    if not files:
        return "No files to zip.", 404
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for f in files:
            zipf.write(os.path.join(SAVE_FOLDER, f), arcname=f)
    zip_buffer.seek(0)
    return send_file(zip_buffer, as_attachment=True, download_name="all_files.zip")

@app.route("/shutdown", methods=["POST"])
def shutdown():
    func = request.environ.get("werkzeug.server.shutdown")
    if func:
        func()
    return "Server shutting down..."

def run_flask(ip, port):
    app.run(host=ip, port=int(port))

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


# -------- Tkinter GUI --------
class DownloaderGUI:
    def __init__(self, root):
        root.title("🎵 YouTube Converter Server")
        root.geometry("650x720")

        # Save folder
        tk.Label(root, text="📂 Save Folder:").pack(pady=4)
        self.folder_var = tk.StringVar(value=SAVE_FOLDER)
        tk.Label(root, textvariable=self.folder_var).pack()
        tk.Button(root, text="Browse Folder", command=self.choose_folder).pack(pady=6)

        # Links (multi)
        tk.Label(root, text="🔗 YouTube Links (space/newline separated):").pack(pady=4)
        self.url_entry = tk.Text(root, width=60, height=6)
        self.url_entry.pack()

        # Quality
        tk.Label(root, text="🎚 Quality:").pack(pady=4)
        self.quality_var = tk.StringVar(value="720")
        ttk.Combobox(
            root, textvariable=self.quality_var,
            values=["144", "240", "360", "480", "720", "1080", "1440", "2160", "4320"],
            state="readonly"
        ).pack()

        # Format
        tk.Label(root, text="🎵 Format:").pack(pady=4)
        self.format_var = tk.StringVar(value="mp4")
        ttk.Combobox(root, textvariable=self.format_var, values=["mp4", "mp3"], state="readonly").pack()

        # Buttons
        tk.Button(root, text="Convert (Videos only)", command=self.convert_videos_only).pack(pady=10)
        tk.Button(root, text="Download Playlist (if link is a playlist)", command=self.download_playlist_mode).pack(pady=6)

        # Progress
        tk.Label(root, text="📊 Progress:").pack(pady=4)
        self.progress = ttk.Progressbar(root, length=400, mode="determinate")
        self.progress.pack()
        self.playlist_label = tk.Label(root, text="Playlist: 0/0")
        self.playlist_label.pack(pady=4)

        # Server controls
        tk.Label(root, text="🌐 Server IP:").pack(pady=4)
        self.ip_entry = tk.Entry(root, width=18)
        self.ip_entry.insert(0, "0.0.0.0")
        self.ip_entry.pack()

        tk.Label(root, text="🔌 Port:").pack(pady=4)
        self.port_entry = tk.Entry(root, width=10)
        self.port_entry.insert(0, "5000")
        self.port_entry.pack()

        tk.Button(root, text="🚀 Start Server", command=self.start_server).pack(pady=8)
        tk.Button(root, text="🛑 Stop Server", command=self.stop_server).pack()

        self.qr_label = tk.Label(root)
        self.qr_label.pack(pady=10)

        # periodic UI progress update (basic)
        root.after(1000, self.update_progress_ui)

    def choose_folder(self):
        global SAVE_FOLDER
        folder = filedialog.askdirectory()
        if folder:
            SAVE_FOLDER = folder
            self.folder_var.set(folder)

    def convert_videos_only(self):
        self._download(playlist_mode=False)

    def download_playlist_mode(self):
        self._download(playlist_mode=True)

    def _download(self, playlist_mode=False):
        raw = self.url_entry.get("1.0", tk.END).strip()
        urls = raw.split()
        quality = self.quality_var.get()
        fmt = self.format_var.get()

        if not urls:
            messagebox.showerror("Error", "Enter at least one YouTube link")
            return

        saved_files = []
        for url in urls:
            if fmt == "mp4":
                ydl_opts = {
                    "progress_hooks": [hook],
                    "outtmpl": os.path.join(SAVE_FOLDER, "%(title)s.%(ext)s"),
                    "format": f"bestvideo[height={quality}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                    "merge_output_format": "mp4",
                    "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
                    "noplaylist": not playlist_mode,
                }
            else:
                ydl_opts = {
                    "progress_hooks": [hook],
                    "outtmpl": os.path.join(SAVE_FOLDER, "%(title)s.%(ext)s"),
                    "format": "bestaudio/best",
                    "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}],
                    "noplaylist": not playlist_mode,
                }

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if isinstance(info, dict) and "entries" in info and info["entries"]:
                        progress_data["playlist_count"] = len(info["entries"])
                        for entry in info["entries"]:
                            title = entry.get("title", "video")
                            saved_files.append(os.path.join(SAVE_FOLDER, f"{title}.{fmt}"))
                    else:
                        title = info.get("title", "video")
                        saved_files.append(os.path.join(SAVE_FOLDER, f"{title}.{fmt}"))
            except Exception as e:
                saved_files.append(f"Error for {url}: {e}")

        # Show summary
        if progress_data["playlist_count"] > 0:
            self.playlist_label.config(text=f"Playlist: {progress_data['playlist_index']}/{progress_data['playlist_count']}")
        messagebox.showinfo("Done", "Saved files:\n" + "\n".join(saved_files))

    def update_progress_ui(self):
        try:
            # Very simple local indicator based on global data
            size = progress_data.get("size", 0) or 1
            downloaded = progress_data.get("downloaded", 0)
            percent = int(downloaded / size * 100)
            self.progress["value"] = percent
            pc = progress_data.get("playlist_count", 0)
            pi = progress_data.get("playlist_index", 0)
            if pc > 0:
                self.playlist_label.config(text=f"Playlist: {pi}/{pc}")
        except:
            pass
        # schedule again
        self.progress.after(1000, self.update_progress_ui)

    def start_server(self):
        ip = self.ip_entry.get()
        port = self.port_entry.get() or "5000"
        display_ip = get_local_ip() if ip == "0.0.0.0" else ip

        # Start Flask in background thread
        global server_thread
        server_thread = threading.Thread(target=run_flask, args=(ip, port), daemon=True)
        server_thread.start()

        messagebox.showinfo("Server", f"Server started at http://{display_ip}:{port}")
        self.show_qr(display_ip, port)

    def stop_server(self):
        port = self.port_entry.get() or "5000"
        try:
            requests.post(f"http://127.0.0.1:{port}/shutdown")
            messagebox.showinfo("Server", "✅ Server stopped successfully.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to stop server: {e}")

    def show_qr(self, ip, port):
        url = f"http://{ip}:{port}"
        qr_img = qrcode.make(url).resize((200, 200))
        qr_tk = ImageTk.PhotoImage(qr_img)
        self.qr_label.config(image=qr_tk)
        self.qr_label.image = qr_tk


# -------- Main --------
if __name__ == "__main__":
    root = tk.Tk()
    gui = DownloaderGUI(root)
    root.mainloop()