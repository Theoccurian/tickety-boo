# Setting up Tickety-Boo News

No coding needed; everything happens in your web browser. Set aside an evening for parts 1–4 and another for parts 5–7.

**Running costs:** the domain is about £12 a year (.co.uk plus .com). Hosting, the robot, Instagram posting and the newsletter (up to 10,000 subscribers) are free. The AI that writes the stories is pay-as-you-go: expect a few pounds a month, with a spending cap so it can never surprise you.

**How it fits together:**
- **GitHub** stores the site and runs the robot on a schedule. It's where you approve stories.
- **Cloudflare** owns your domain and hosts the website.
- **Anthropic** provides the AI that writes the stories.
- **Instagram** gets each approved story as a 3-slide post.
- **Kit** collects newsletter subscribers and sends the Friday email.

---

## Part 1: Claim the name (15 minutes)

1. Create a free account at **dash.cloudflare.com**.
2. Go to **Domain Registration → Register Domains**, search for `ticketyboonews`, and buy the **.co.uk** (and the **.com** if you want it too, so nobody else can take it).
3. In the Instagram app, create the account **@ticketyboonews**. You don't need to post anything yet.
4. Turn on two-step login (2FA) for Cloudflare, and later for GitHub, Meta and Kit too. Between them these accounts control your site, so they're worth protecting.

## Part 2: Set up the robot (30 minutes)

### 2a. GitHub
1. Sign up at **github.com**.
2. Click **+ → New repository**, name it `tickety-boo`, choose **Public**, and click **Create repository**. Public keeps the robot free to run; nothing private is stored in it.
3. Upload the files. The download contains a hidden `.github` folder that **must** be included, so show hidden files first:
   - **Mac:** in Finder press **Cmd + Shift + .** (full stop)
   - **Windows:** File Explorer → **View → Show → Hidden items**

   Click **uploading an existing file**, drag in **everything inside** the `tickety-boo` folder, and click **Commit changes**. Check that a `.github` folder now appears in the list.
4. **Settings → Actions → General → Workflow permissions:** choose **Read and write permissions**, tick **Allow GitHub Actions to create and approve pull requests**, and click **Save**.
5. **Settings → General:** scroll to *Pull Requests* and tick **Automatically delete head branches**. This keeps things tidy after each approval.

### 2b. The AI key
1. At **console.anthropic.com**, create an account and add a little credit.
2. Under **Limits**, set a monthly cap, e.g. £10.
3. **API Keys → Create Key**, then copy it.

### 2c. The Cloudflare keys
1. In Cloudflare, click your profile icon → **My Profile → API Tokens → Create Token**, and use the **Edit Cloudflare Workers** template. Leave the settings as they are and click through to **Create Token**, then copy it.
2. Find your **Account ID**: on the Cloudflare home page, open the **⋯** menu next to your account name and choose **Copy account ID**.

### 2d. Put the keys in GitHub
In your repository: **Settings → Secrets and variables → Actions → New repository secret**. Add three secrets, with the names exactly as written:

| Name | Secret |
|---|---|
| `ANTHROPIC_API_KEY` | the Anthropic key |
| `CLOUDFLARE_API_TOKEN` | the Cloudflare token |
| `CLOUDFLARE_ACCOUNT_ID` | the account ID |

### 2e. Tell the site its address
Click `config.yaml`, then the pencil icon. Set:
- `base_url: "https://ticketyboonews.co.uk"`
- `contact_email:` an address for corrections and partnership enquiries

Then click **Commit changes**.

## Part 3: Put the website online (15 minutes, then up to an hour's wait)

1. In GitHub open **Actions → Publish website → Run workflow**. After about 3 minutes it shows a green tick.
2. In Cloudflare go to **Workers & Pages → tickety-boo → Settings → Domains & Routes → Add → Custom domain**. Enter `ticketyboonews.co.uk` and add it. Cloudflare sets everything up itself, padlock included.
3. **Send the other addresses to the main one**, so Google sees one site rather than copies:
   - **www:** in your domain's **DNS → Records**, add a **CNAME** record with name `www`, target `ticketyboonews.co.uk`, and proxy **on** (orange cloud). Then go to **Rules → Redirect Rules**, choose **Create from template → Redirect from WWW to root**, and deploy it.
   - **.com (if you bought it):** this one has a couple more steps. Ask Claude to set it up with you; it takes 5 minutes.
4. **Let search engines and AI tools in.** Cloudflare has started blocking some AI crawlers by default on sites that show ads, and doing so can also block Google's and Bing's crawlers. In Cloudflare, open your domain, find **AI Crawl Control** (in the Security area), and make sure:
   - **Search** crawlers are **allowed**
   - **Training** crawlers are **allowed** (or set to match `allow_ai_training` in `config.yaml`)
   - **Managed robots.txt** is **off**, so the site's own robots.txt is used

   Cloudflare changes this screen from time to time. If it looks different, ask Claude to go through it with you.
5. **Give yourself an email address on the domain (free).** In your domain go to **Email → Email Routing**, switch it on, and create `hello@ticketyboonews.co.uk`, forwarding to your normal email. Put that address in `config.yaml` as `contact_email`.
6. Optional but recommended: **Analytics & Logs → Web Analytics → Add a site**. Copy the token into `config.yaml` under `analytics → cloudflare_token`. That gives you visitor numbers for sponsors, with no cookie banner needed.

## Part 4: Your first stories (5 minutes)

1. **Actions → Find good news → Run workflow.** It takes 2–4 minutes.
2. Open the run and click the **Find and write stories** step. Each news source shows how many headlines it found. A source that says "Couldn't read feed" has moved; tell Claude which one.
3. Open the **Pull requests** tab. Each story is waiting there; approve a few (see "Approving stories" below).
4. A few minutes later they're live on **ticketyboonews.co.uk**.

---

## Part 5: Instagram (20 minutes)

1. **Profile:** use `static/brand/instagram-profile.png` from the download as the picture. For the bio, try *Good news from around the world. Every story checked and sourced.*, and add your website as the link.
2. **Professional account:** in Instagram, **Settings → Account type and tools → Switch to professional account**. Choose **Creator**, with a category such as *News & media website*.
3. **Meta developer app:** go to **developers.facebook.com** and log in with Facebook. Then **My Apps → Create App**, choose the use case for managing **content on Instagram**, and name it "Tickety-Boo poster". In the app open **Instagram → API setup with Instagram login → Generate access tokens → Add account**, log in as @ticketyboonews, then click **Generate token** and copy it. Leave the app in development mode.
4. **Two more GitHub secrets:**
   - `INSTAGRAM_ACCESS_TOKEN`: the token you just copied
   - `INSTAGRAM_TOKEN_KEY`: any long random text of 30+ characters. You don't need to remember it; just never change it. The robot uses it to lock away the token it renews each week.
5. In `config.yaml`, set `instagram → enabled: true`, and put your Instagram page link in `instagram_url`.
6. **Actions → Post to Instagram → Run workflow → "test connection only".** A green tick that says "Connected to Instagram as @ticketyboonews" means it's working.

From then on, each approved story is posted as a 3-slide carousel (headline, In brief, Why it matters). Posts are at least 3 hours apart, between 8am and 9pm.

## Part 6: Newsletter and donations (20 minutes)

**Newsletter (Kit, free up to 10,000 subscribers):**
1. Sign up at **kit.com**. Then **Grow → Landing pages & forms → Create new → Form**. Any style will do; it's only used for its address.
2. Open the form, choose **Embed → HTML**, and copy the address after `action="` (it looks like `https://app.kit.com/forms/1234567/subscriptions`). Put it in `config.yaml` under `newsletter → form_action`.
3. In Kit, go to **Settings → Developer**, create a **v4 API key**, and add it in GitHub as the secret `KIT_API_KEY`.
4. In `config.yaml`, set `newsletter → weekly_digest: true`.
5. **Test the sign-up:** once the site has rebuilt, enter your own email in the box on the site. You should get Kit's confirmation email. If the form shows an error instead, put Kit's landing-page link in `subscribe_url` and clear `form_action`; the site will show a Subscribe button instead.
6. **Help your emails reach inboxes:** in Kit, under **Settings → Email** (sending domain or domain authentication), add `ticketyboonews.co.uk` and set the sender to `hello@ticketyboonews.co.uk`. Kit shows a few DNS records; add them in Cloudflare under **DNS → Records**. Without this, Gmail and Outlook are more likely to put the newsletter in spam.

Every Friday morning the robot builds that week's newsletter from the published stories, including any partner discount codes, and saves it as a **draft in Kit**. You read it, then press send. Once you trust it, set `auto_send: true` and it sends itself.

**Donations:** create a page on **ko-fi.com** (or Buy Me a Coffee) and put the link in `config.yaml` under `support → donate_url`.

## Part 7: Search engines (10 minutes)

1. **Google Search Console** (search.google.com/search-console): add a **Domain** property for `ticketyboonews.co.uk`. Because the domain is on Cloudflare, Google can verify it automatically. Then go to **Sitemaps** and submit `sitemap.xml` and `news-sitemap.xml`.
2. **Bing Webmaster Tools** (bing.com/webmasters): choose **Import from Google Search Console**. Bing also powers Microsoft Copilot and feeds DuckDuckGo.
3. **Google News** (optional, after a few weeks of stories): add the site at **publishercenter.google.com**.

Google traffic builds slowly, typically over 3–6 months. New stories are announced to Bing automatically the moment they're published.

---

## Approving stories (your 5 minutes a day)

Install the **GitHub** app on your phone. New stories arrive around 7am, 1pm and 7pm, and each one shows the full article, the "In brief" points, the Instagram caption and a link to the original report.

- **Publish:** tap **Merge pull request → Confirm merge**. It's live within a few minutes, then joins the Instagram queue and Friday's newsletter.
- **Bin it:** tap **Close pull request**.
- **Tweak first:** open **Files changed → ⋯ → Edit file**, save, then merge.

Stories you ignore are cleared away after 5 days.

**Correcting a published story:** edit it in `content/articles` (pencil icon) and add a line `updated: 2026-11-02` (today's date) under the `date:` line at the top. The page then shows search engines that it was corrected.

**Removing a published story:** open `content/articles`, click the story, choose **⋯ → Delete file**, and commit. Delete the Instagram post by hand if it has already gone out.

## Adding a sponsor

Add the partner under `partners:` in `config.yaml` (there's an example in the file). The offer appears on the site's "Offers for TBN readers" page, labelled as a paid partnership as UK advertising rules (the ASA's CAP Code) require. With `codes_for_subscribers_only: true`, the discount code appears only in the newsletter, which gives people a reason to subscribe.

Sponsors will ask for your numbers: monthly visitors (Cloudflare Web Analytics), newsletter subscribers (Kit) and Instagram followers.

## Settings you might change (`config.yaml`)

| Setting | What it does |
|---|---|
| `articles_per_run` | Stories written each run (it runs 3 times a day) |
| `min_score` | How picky the robot is, from 1 to 10. Raise it for fewer, stronger stories |
| `auto_publish` | `true` skips your approval. Only switch it on once you trust the robot |
| `instagram` | Posting hours, the gap between posts, carousel on or off, hashtags |
| `newsletter` | Weekly digest on or off, draft or auto-send, intro line |
| `allow_ai_training` | `false` stops AI companies training on your stories; AI search tools can still cite you |
| `categories`, `feeds` | Sections, and the news sources the robot reads |

## If something goes wrong

GitHub emails you when a run fails. Open **Actions** and click the red ✕ to see why.

- **"…is not set" or "…is missing":** a secret is missing or misspelt. Check the names in parts 2d, 5 and 6.
- **Publish fails at "Publish to Cloudflare":** check `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.
- **"Instagram said: …" about the token or session:** the connection was broken, often by an Instagram password change. Generate a new token (part 5, step 3) and replace `INSTAGRAM_ACCESS_TOKEN`. Leave `INSTAGRAM_TOKEN_KEY` alone.
- **"Kit said …":** check `KIT_API_KEY`.
- **No stories appear:** nothing scored highly enough that time. Lower `min_score` a little, or add news sources.

## Good to know

- Stories are dated from the moment you approve them, so a story you approve a day late still counts as new on the site, Instagram, the newsletter and in search engines.
- Each story is a short summary in our own words with a credit and link, which is how good-news round-ups normally work. If a source ever asks you not to use its stories, remove it from `feeds` in `config.yaml`.

- The site has a privacy page. If you collect newsletter sign-ups as a business, check on **ico.org.uk** whether you need to pay the ICO's data protection fee. It's usually a small yearly amount, and some people are exempt.
- This guide isn't legal or financial advice. If sponsorship income grows, it's worth a quick chat with an accountant about how to declare it.
