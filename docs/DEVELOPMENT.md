# Development

```bash
cd backend
pip install -r requirements.txt
python -m unittest discover -s tests -t .        # backend tests
python -m tidyfin.devtools.devserver             # full app on http://127.0.0.1:8585, sign in: admin / pass
python -m tidyfin.devtools.devserver --empty     # same, starting with the setup wizard
cd ../frontend && node dev/check_i18n.mjs        # translation consistency
find js -name '*.js' -print0 | xargs -0 -n1 node --check   # JavaScript syntax
```

The dev server runs the real application against in-memory fake Jellyfin, Radarr and Sonarr with fictional titles, so you can work on the dashboard without any of them. The posters are drawn by `backend/tidyfin/devtools/artwork.py`. The dashboard is plain JavaScript modules and CSS, no build step. Translations live in `frontend/js/locales/` (English and Polish; every key must exist in both).
