# Voice SDK asset

`vapi-web-2.7.1.js` bundles the official `@vapi-ai/web` npm release 2.7.1 and its dependencies.
It is served locally so a CDN widget cannot add a second call button or change versions
underneath the demo. Dependency license notices are retained at the end of the bundle.

Rebuild with Node in a temporary directory:

```sh
npm install --no-audit --no-fund @vapi-ai/web@2.7.1 esbuild@0.25.12
```

Create `sdk-entry.js`:

```js
import Vapi from '@vapi-ai/web';
window.Vapi = Vapi;
```

```sh
npx esbuild sdk-entry.js --bundle --minify --format=iife --platform=browser --legal-comments=eof --outfile=vapi-web-2.7.1.js
```

If changing versions, update the asset filename, server route and script tag together.

Upstream: https://github.com/VapiAI/client-sdk-web (MIT license).
