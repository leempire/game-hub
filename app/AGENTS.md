This is an Expo/React Native mobile application. Prioritize mobile-first patterns, performance, and cross-platform compatibility.

## Expo has changed — do not trust your training data

Expo ships breaking changes every SDK release. APIs you remember are likely renamed, moved, or removed. Before writing any code that touches an Expo, EAS, or React Native API:

1. Read the major version of the `expo` package in `package.json`.
2. Fetch the matching versioned docs: `https://docs.expo.dev/versions/v<major>.0.0/`
3. For anything else, fetch https://docs.expo.dev/llms.txt — an index of all Expo docs with corrections to common LLM misconceptions. Follow its links to the specific page you need; never answer from memory.

## Commands

Use `bunx` instead of `npx` if the project uses bun (`bun.lock` present).

```bash
npx expo install <package>  # ALWAYS use instead of npm/yarn/pnpm/bun add — resolves SDK-compatible versions
../scripts/start.sh web     # start the project web server (from app/)
npx expo lint               # lint
npx tsc --noEmit            # typecheck
npx expo-doctor             # diagnose dependency and config issues
npx expo install --fix      # fix incompatible package versions
```

Run lint and typecheck before declaring any task done.

## Navigation & Routing

- This project uses **React Navigation**: native stack and bottom tabs are registered in `src/navigation.tsx`; screens live in `src/screens/`.
- Keep new routes aligned across `src/navigation.tsx`, `App.tsx` linking paths, and `src/deepLink.ts`. Game screens also need both return paths through `src/backGuard.tsx`; see the root `CLAUDE.md`.

## Android builds and releases

The maintainer builds and deploys APKs locally on the server using `bash bin/release-apk.sh <version>` from the repository root. The script handles version updates, Expo prebuild, Gradle, and APK deployment. Contributors prepare tested changes and a PR; follow `../docs/协作开发.md` for the full release workflow. Use `../scripts/{start,stop,restart,status}.sh` to manage this project's services.

## Rules

- If `ios/` and `android/` directories do not exist, they are generated (Continuous Native Generation). Never create or edit them by hand — configure native behavior in `app.json` and config plugins.
- Expo Go only includes its bundled native modules. After adding a library with native code, validate it in a development build, following the project's local build workflow.
- Prefer recommended Expo modules over third-party libraries, and check your available skills before adding dependencies. Docs: https://docs.expo.dev/versions/latest/index.md
