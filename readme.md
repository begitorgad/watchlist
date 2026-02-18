# Watchlist (local, no server) — PySide6 + SQLite + TMDB
A small personal watchlist app that runs locally on your PC (Windows/Linux). It stores everything in a local SQLite database and can fetch metadata (year/runtime/genres) from TMDB only when you add a new title.



# Features

## Adding / Searching

- Search TMDB (movies + TV) and pick the correct match from a list. 
- Add local without TMDB (choose type: movie / show / youtube). 
- Search local for matches already in your DB. 
- Prevents duplicates using normalized titles and (type, tmdb_id) for TMDB items. 

## Managing your list

- Mark entries seen/unseen (seen items can be greyed; tagged items are colored, and seen+tagged becomes a greyed version of the tag color). 
- Delete entries. 
- Filters: unseen-only, type, genre, limit. 
- Sorting: Title (A→Z), runtime ascending/descending. 
- Random pick (optionally filtered). 

## Tags / Marks

- Create/delete tags (name + color). 
- Assign tags to a selected media (checkbox list). 
- Rows are colored using the tag color (and blended to grey if the media is seen). 


# Project structure

- gui.py — the GUI app (PySide6/Qt). 
- watch_core.py — database + TMDB client + service layer. 
- watchlist.sqlite3 — created automatically next to the scripts on first run. 


# Requirements

- Python 3.10+ (tested on Python 3.12)
- Dependencies:
    - PySide6
    - requests

## Install:

`pip install PySide6 requests`


# TMDB setup (optional but recommended)

The app uses TMDB v4 Read Access Token via environment variable TMDB_TOKEN. If it’s missing, TMDB add can fall back to “Add local” behavior. 

## Linux/macOS

`export TMDB_TOKEN="YOUR_TMDB_V4_READ_ACCESS_TOKEN"`

## Windows (PowerShell)

`setx TMDB_TOKEN "YOUR_TMDB_V4_READ_ACCESS_TOKEN"`

(Then restart your terminal / log out-in so it’s visible to apps.)

# Run

`python3 gui.py` or `python gui.py`

# Release

If you instead want a prebuilt Linux/Windows executable it is available in release