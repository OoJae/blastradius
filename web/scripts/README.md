# og-card.html

The source for `public/og.png`. It is not served — rendering it is a manual
step, because the card only changes when the hero copy does.

To regenerate, with the API serving `web/dist` on :8000:

```bash
cp scripts/og-card.html dist/og-source.html   # point its <link> at the built CSS first
# screenshot http://127.0.0.1:8000/og-source.html at exactly 1200x630
rm dist/og-source.html
```
