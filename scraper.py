#!/usr/bin/env python3
import argparse, json, os, re, sys, time
from urllib.parse import urlparse
from pathlib import Path

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from readability import Document as ReadabilityDoc
from markdownify import markdownify as md
import pandas as pd

# ----------------------
# Helpers
# ----------------------
def slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-]+", "", s)  # keep ascii letters, numbers, dashes
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "page"

def unique_path(base_dir: Path, base_name: str, ext: str = ".md") -> Path:
    """Return a unique path like base_name.md, base_name-2.md, ..."""
    p = base_dir / f"{base_name}{ext}"
    if not p.exists():
        return p
    i = 2
    while True:
        candidate = base_dir / f"{base_name}-{i}{ext}"
        if not candidate.exists():
            return candidate
        i += 1

def ensure_dirs(out_dir: Path):
    (out_dir / "assets").mkdir(parents=True, exist_ok=True)
    (out_dir / "data").mkdir(parents=True, exist_ok=True)

def to_markdown_table(df: pd.DataFrame) -> str:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df.to_markdown(index=False)

def collect_headings(soup: BeautifulSoup):
    headings = []
    for lvl in range(1, 7):
        for h in soup.select(f"h{lvl}"):
            text = " ".join(h.get_text(" ", strip=True).split())
            if text:
                headings.append((lvl, text))
    return headings

# ----------------------
# Core scrape function
# ----------------------
def scrape_url(url: str, out_dir: Path, wait: int = 0, scroll: bool = False, screenshot: bool = False):
    ensure_dirs(out_dir)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60_000)

        if scroll:
            for _ in range(30):
                page.mouse.wheel(0, 1200)
                time.sleep(0.25)

        if wait > 0:
            page.wait_for_timeout(wait)

        html = page.content()
        soup = BeautifulSoup(html, "lxml")

        # Title & meta
        title = (page.title() or "").strip()
        meta_desc = ""
        md_tag = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
        if md_tag and md_tag.get("content"):
            meta_desc = md_tag["content"].strip()

        # Headings
        headings = collect_headings(soup)

        # Main content
        try:
            rdoc = ReadabilityDoc(html)
            main_html = rdoc.summary(html_partial=True)
            if not title:
                title = (rdoc.short_title() or "").strip()
        except Exception:
            main_html = str(soup.body or soup)

        main_md = md(main_html, strip=["script", "style"], heading_style="ATX")

        # Tables
        tables = []
        try:
            dfs = pd.read_html(html, flavor="lxml")
            tables = [to_markdown_table(df) for df in dfs]
        except Exception:
            pass

        # Decide file names from **title**, not URL
        if not title:
            # final fallback: use hostname/path
            parsed = urlparse(url)
            title = (parsed.netloc + " " + parsed.path).strip() or "page"
        base_name = slugify(title)[:80] or "page"  # keep it sane length
        md_path = unique_path(out_dir, base_name)

        screenshot_rel = None
        if screenshot:
            shot_path = unique_path(out_dir / "assets", base_name, ext=".png")
            page.screenshot(path=str(shot_path), full_page=True)
            screenshot_rel = f"assets/{shot_path.name}"

        browser.close()

    # Build Markdown
    lines = []
    lines.append(f"# {title}\n")
    lines.append(f"> Source: {url}\n")
    if meta_desc:
        lines.append(f"> Meta description: {meta_desc}\n")

    if headings:
        lines.append("\n## Table of contents")
        for lvl, text in headings:
            indent = "  " * (lvl - 1)
            anchor = "#" + slugify(text)
            lines.append(f"{indent}- [{text}]({anchor})")
        lines.append("")

    lines.append("\n---\n")
    lines.append(main_md.strip())

    for i, tbl in enumerate(tables, 1):
        lines.append(f"\n---\n\n### Table {i}\n")
        lines.append(tbl)

    if screenshot_rel:
        lines.append(f"\n---\n\n![Screenshot]({screenshot_rel})")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ Saved {md_path}")

# ----------------------
# Main (URLs.txt, max 5)
# ----------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", default="URLs.txt", help="Path to file with one URL per line")
    ap.add_argument("--outdir", default="out", help="Output directory for Markdown files")
    ap.add_argument("--wait", type=int, default=2000, help="Extra wait in ms after network idle")
    ap.add_argument("--scroll", action="store_true", help="Auto-scroll to load lazy content")
    ap.add_argument("--screenshot", action="store_true", help="Save screenshots")
    args = ap.parse_args()

    out_dir = Path(args.outdir)
    urls_file = Path(args.urls)
    if not urls_file.exists():
        print(f"❌ URLs file not found: {urls_file}")
        sys.exit(1)

    with open(urls_file, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]

    for u in urls[:5]:
        try:
            scrape_url(u, out_dir, wait=args.wait, scroll=args.scroll, screenshot=args.screenshot)
        except Exception as e:
            print(f"⚠️ Failed on {u}: {e}")

if __name__ == "__main__":
    main()
