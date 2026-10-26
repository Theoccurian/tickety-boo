# Tickety-Boo News

An automatic good-news website, Instagram account and weekly newsletter.

Three times a day a robot reads trusted news sources, picks the genuinely positive stories, and writes short original articles, "In brief" summaries and Instagram captions with AI. It fact-checks each one against the original report and sends it to you for a one-tap approval. Approved stories go live on the website (hosted on Cloudflare), are posted to Instagram as 3-slide carousels spaced through the day, and are gathered into a Friday newsletter in Kit.

**To get started, follow [SETUP.md](SETUP.md).** Day-to-day settings live in [config.yaml](config.yaml).

| Folder | What's in it |
|---|---|
| `content/articles/` | The published stories (one file each) |
| `config.yaml` | Name, sections, news sources, Instagram, newsletter, partners, search settings |
| `scripts/` | The robot, site builder, image maker, Instagram poster, newsletter builder and search pings |
| `templates/`, `static/` | The site's design, fonts and the Instagram profile picture (`static/brand/`) |
| `.github/workflows/` | The schedules that run everything |
| `wrangler.jsonc` | Cloudflare hosting settings |
