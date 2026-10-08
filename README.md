# Venture

Venture is a lightweight purple search homepage inspired by the simplicity of DuckDuckGo.

## Features

- Clean purple search interface
- Direct URL navigation from the search box
- Search provider choices: DuckDuckGo, Google, Bing, and Brave Search
- Light/dark theme toggle
- Optional opening of results in a new tab
- Responsive layout for desktop and mobile
- No build step or dependencies

## Run locally

Open `index.html` directly, or serve the folder with any static web server.

Example:

```bash
python3 -m http.server 0826
```

Then open `http://localhost:0826`.

## Notes

Venture currently acts as a search front-end and delegates result retrieval to the selected provider. A future backend can add first-party result aggregation, ranking, instant answers, and privacy proxying.
