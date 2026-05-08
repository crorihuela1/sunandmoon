# Sun & Moon at 30A — Deploy Package

This is a self-contained, ready-to-deploy build of the Sun & Moon at 30A website.

## What's in here

```
.
├── index.html            ← homepage (was sunandmoon-v2.html)
├── events.html           ← 30A events calendar
├── favicon.png           ← favicon / apple touch icon
├── robots.txt            ← search-engine instructions
├── sitemap.xml           ← search-engine sitemap
├── _headers              ← Cloudflare Pages cache + security headers
├── images/
│   ├── sunset-seagrove.jpg     ← hero slide 1
│   ├── aerial-30a.jpg          ← hero slide 2
│   └── logos/
│       ├── sunmoon-color.png
│       ├── sunmoon-white.png
│       ├── sun.png
│       └── moon.png
└── photos/
    ├── blue-moon/{001,010,020,040,060,080}.jpg
    └── golden-sun/{001,005,015,030,045}.jpg
```

Total: ~11 MB, 21 files. Fully static — no backend, no build step.

## Deploy to Cloudflare Pages

### Option A — Direct upload (no setup, fastest one-time deploy)
1. Go to https://dash.cloudflare.com (sign up / log in)
2. Workers & Pages → Create application → Pages tab → "Upload assets"
3. Project name: `sun-and-moon-30a`
4. Drag this entire folder onto the upload area
5. Click "Deploy site". You get `sun-and-moon-30a.pages.dev` in ~30 seconds.

### Option B — Wrangler CLI (best for re-deploys)
```bash
npm install -g wrangler
wrangler login
cd /path/to/this/folder
wrangler pages deploy . --project-name=sun-and-moon-30a
```
Each subsequent edit only needs the `wrangler pages deploy` line.

### Option C — Connect to GitHub (best for ongoing edits)
1. Push this folder to a new GitHub repo
2. Cloudflare Pages → Connect to Git → pick the repo → Build command: (none) → Output dir: `/`
3. Every push to `main` triggers an automatic deploy.

## Custom domain (sunandmoonat30a.com)

After the first deploy, in your Cloudflare Pages project:
- Custom domains → Set up a custom domain → enter `sunandmoonat30a.com`
- Cloudflare will tell you what nameserver / CNAME records to set at your registrar
- Propagation takes 5–30 minutes; SSL is automatic
