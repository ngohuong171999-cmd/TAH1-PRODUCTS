#!/usr/bin/env python3
"""
Tạo trang riêng cho từng sản phẩm để link chia sẻ (Zalo, Facebook, Messenger...) có
ảnh xem trước vuông 1:1, tiêu đề = PRODUCT_NAME, dòng phụ = PROV_COD.

Đọc danh sách sản phẩm đang ON từ Apps Script (?list=1), rồi với mỗi sản phẩm tạo:
    p/<PROV_COD>/index.html   trang sản phẩm (là bản sao của index.html + thẻ Open Graph)
    p/<PROV_COD>/thumb.jpg    ảnh xem trước 600x600 (lấy từ cột PHOTO_DIM, hoặc PHOTO_1)
và file share-links.csv liệt kê link chia sẻ của từng sản phẩm.
"""
import csv, html, io, json, os, re, shutil, sys, urllib.parse, urllib.request
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("Thiếu thư viện Pillow: pip install pillow")

SCRIPT_URL = os.environ.get(
    "SCRIPT_URL",
    "https://script.google.com/macros/s/AKfycbyxdPYA4M5Gt6Fir8hhXeovXCtSJ2wVDaKFkeYVeG6UzjynWAIDGvGCn2H6HUDpVGnY/exec",
)
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "p"
THUMB = 600
UA = {"User-Agent": "Mozilla/5.0 (TanHoa page builder)"}


def base_url():
    b = os.environ.get("BASE_URL")
    if b:
        return b.rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "")          # dạng owner/name
    if "/" in repo:
        owner, name = repo.split("/", 1)
        if name.lower() == owner.lower() + ".github.io":
            return f"https://{owner.lower()}.github.io/"
        return f"https://{owner.lower()}.github.io/{name}/"
    return "http://localhost:8000/"


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def norm_key(k):
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def drive_id(u):
    m = re.search(r"drive\.google\.com/.*?/d/([\w-]+)", u) or re.search(r"drive\.google\.com/.*?[?&]id=([\w-]+)", u)
    return m.group(1) if m else None


def image_candidates(u):
    did = drive_id(u)
    if did:
        return [
            f"https://lh3.googleusercontent.com/d/{did}=w1200",
            f"https://drive.google.com/thumbnail?id={did}&sz=w1200",
            f"https://drive.google.com/uc?export=view&id={did}",
        ]
    return [u]


def make_thumb(url, dest):
    """Tải ảnh, đặt vào khung vuông nền trắng (không cắt mất chú thích kích thước), lưu JPG 600x600."""
    for cand in image_candidates(url):
        try:
            im = Image.open(io.BytesIO(http_get(cand)))
            im.load()
        except Exception as e:
            print(f"    không tải được {cand[:80]}: {e}")
            continue
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        side = max(im.size)
        sq = Image.new("RGB", (side, side), "white")
        sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        sq = sq.resize((THUMB, THUMB), Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        sq.save(dest, "JPEG", quality=88, optimize=True, progressive=True)
        return True
    return False


def clean_name(name):
    return re.sub(r"\s*#\s*", " / ", str(name)).strip()


def og_block(name, code, page_url, img_url):
    e = lambda s: html.escape(str(s), quote=True)
    tags = [
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="Tan Hoa 1 Co., Ltd.">',
        f'<meta property="og:title" content="{e(name)}">',
        f'<meta property="og:description" content="{e(code)}">',
        f'<meta property="og:url" content="{e(page_url)}">',
        f'<meta name="description" content="{e(code)}">',
    ]
    if img_url:
        tags += [
            f'<meta property="og:image" content="{e(img_url)}">',
            '<meta property="og:image:type" content="image/jpeg">',
            f'<meta property="og:image:width" content="{THUMB}">',
            f'<meta property="og:image:height" content="{THUMB}">',
            '<meta name="twitter:card" content="summary">',
            f'<meta name="twitter:image" content="{e(img_url)}">',
        ]
    tags += [f'<meta name="twitter:title" content="{e(name)}">', f'<meta name="twitter:description" content="{e(code)}">']
    tags.append(f"<script>window.TH_ID={json.dumps(code)};</script>")
    return "\n".join(tags)


def main():
    template = (ROOT / "index.html").read_text(encoding="utf-8")
    if "<!--OG-->" not in template:
        sys.exit("index.html không có dấu <!--OG--> trong <head>.")
    sep = "&" if "?" in SCRIPT_URL else "?"
    data = json.loads(http_get(SCRIPT_URL + sep + "list=1").decode("utf-8"))
    products = data.get("products")
    if data.get("result") != "Success" or products is None:
        sys.exit(f"Apps Script không trả danh sách sản phẩm: {str(data)[:300]}")

    base = base_url()
    OUT.mkdir(exist_ok=True)
    keep, rows = set(), []
    for p in products:
        n = {norm_key(k): v for k, v in p.items()}
        code = str(n.get("provcod", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", code or ""):
            print(f"Bỏ qua mã không hợp lệ: {code!r}")
            continue
        name = clean_name(n.get("productname") or code)
        dim_key = next((k for k in n if re.search(r"photo.*dim|dim.*photo", k)), None)
        src = str((dim_key and n.get(dim_key)) or n.get("photo1") or "").strip()

        folder = OUT / code
        thumb_ok = bool(src) and make_thumb(src, folder / "thumb.jpg")
        if not thumb_ok:
            print(f"  [{code}] chưa có ảnh xem trước (cột PHOTO_DIM / PHOTO_1 trống hoặc không tải được)")
        page_url = f"{base}p/{code}/"
        img_url = f"{page_url}thumb.jpg" if thumb_ok else ""

        page = template.replace("<!--OG-->", og_block(name, code, page_url, img_url), 1)
        page = re.sub(r"<title>.*?</title>", lambda m: f"<title>{html.escape(name)}</title>", page, count=1, flags=re.S)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(page, encoding="utf-8")
        keep.add(code)
        rows.append((code, name, page_url, "OK" if thumb_ok else "THIẾU ẢNH"))
        print(f"  [{code}] {name[:60]}")

    for d in OUT.iterdir():                 # xoá trang của sản phẩm đã OFF / đã xoá khỏi sheet
        if d.is_dir() and d.name not in keep:
            shutil.rmtree(d)
    with open(ROOT / "share-links.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["PROV_COD", "PRODUCT_NAME", "SHARE_LINK", "THUMBNAIL"])
        w.writerows(rows)
    print(f"Xong: {len(rows)} sản phẩm.")


if __name__ == "__main__":
    main()
