import os
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime
from PIL import Image, ExifTags
import img2pdf
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
PICS_DIR = BASE_DIR / "SPA pics"
OUTPUT_PDF = BASE_DIR / "SPA_notes_compiled.pdf"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
DEBOUNCE_SECONDS = 3  # Wait time after last file drop before rebuilding

def get_image_timestamp(image_path: Path) -> float:
    """Extract EXIF 'DateTimeOriginal' / 'DateTimeDigitized' if available, fallback to file mtime."""
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if exif:
                # Find tag ID for DateTimeOriginal (36867) or DateTime (306)
                for tag_id in (36867, 36868, 306):
                    if tag_id in exif:
                        date_str = exif[tag_id]
                        # Standard EXIF date format: "YYYY:MM:DD HH:MM:SS"
                        try:
                            dt = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                            return dt.timestamp()
                        except ValueError:
                            pass
    except Exception:
        pass
    
    # Fallback to filesystem modification time
    return os.path.getmtime(image_path)

def compile_pdf():
    """Sorts all images chronologically and builds the compiled PDF."""
    if not PICS_DIR.exists():
        PICS_DIR.mkdir(parents=True, exist_ok=True)
        return

    # Find valid images
    images = [
        p for p in PICS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not images:
        print("[INFO] No images found in 'SPA pics'. Skipping PDF generation.")
        return

    # Sort images by metadata timestamp
    images.sort(key=get_image_timestamp)
    print(f"[INFO] Found {len(images)} images. Compiling in chronological order...")

    # img2pdf requires RGB/valid image stream
    image_bytes_list = []
    temp_converted = []

    try:
        for img_path in images:
            # Check if image needs format conversion (e.g. RGBA/PNG with alpha channel or non-JPEG)
            with Image.open(img_path) as img:
                if img.mode in ("RGBA", "P", "LA"):
                    rgb_img = img.convert("RGB")
                    temp_path = BASE_DIR / f"_temp_{img_path.stem}.jpg"
                    rgb_img.save(temp_path, "JPEG", quality=95)
                    temp_converted.append(temp_path)
                    image_bytes_list.append(str(temp_path))
                else:
                    image_bytes_list.append(str(img_path))

        # Write PDF safely using img2pdf (lossless & fast)
        with open(OUTPUT_PDF, "wb") as f:
            f.write(img2pdf.convert(image_bytes_list))

        print(f"[SUCCESS] Compiled PDF saved at: {OUTPUT_PDF}")

    except Exception as e:
        print(f"[ERROR] Failed to compile PDF: {e}")
        return
    finally:
        # Cleanup temporary conversion files
        for temp_file in temp_converted:
            if temp_file.exists():
                temp_file.unlink()

    # Push changes to GitHub
    push_to_github()

def push_to_github():
    """Stages, commits, and pushes the updated PDF to the remote repository."""
    try:
        print("[GIT] Committing and pushing updated PDF...")
        subprocess.run(["git", "add", str(OUTPUT_PDF)], cwd=BASE_DIR, check=True)
        
        # Check if there are changes staged
        diff_check = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=BASE_DIR
        )
        if diff_check.returncode == 0:
            print("[GIT] No changes detected in PDF. Skipping commit.")
            return

        commit_msg = f"Auto-update SPA_notes_compiled.pdf [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=BASE_DIR, check=True)
        subprocess.run(["git", "push"], cwd=BASE_DIR, check=True)
        print("[GIT SUCCESS] Remote repository updated.")
    except subprocess.CalledProcessError as e:
        print(f"[GIT ERROR] Command failed: {e}")
    except Exception as e:
        print(f"[GIT ERROR] Unexpected error: {e}")

class ImageFolderHandler(FileSystemEventHandler):
    """Watches for changes with debouncing to prevent multiple rapid executions."""
    def __init__(self):
        self.last_modified = 0

    def on_any_event(self, event):
        if event.is_directory:
            return
        
        ext = Path(event.src_path).suffix.lower()
        if ext in SUPPORTED_EXTENSIONS:
            self.last_modified = time.time()

if __name__ == "__main__":
    PICS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Run an initial compilation on startup
    compile_pdf()

    event_handler = ImageFolderHandler()
    observer = Observer()
    observer.schedule(event_handler, path=str(PICS_DIR), recursive=False)
    observer.start()

    print(f"[RUNNING] Monitoring folder: {PICS_DIR}")

    try:
        while True:
            time.sleep(1)
            # If changes were detected and quiet period has passed
            if event_handler.last_modified > 0:
                if time.time() - event_handler.last_modified > DEBOUNCE_SECONDS:
                    event_handler.last_modified = 0
                    compile_pdf()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()