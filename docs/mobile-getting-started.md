# Create a Hyperview mobile client

New to Hyperview? Start with the [Django-to-Hyperview request lifecycle](quickstart.md#request-lifecycle), including a screen-to-fragment example.

Start with a normal blank Expo application and install Hyperview as a
dependency. You do not need to clone the Hyperview repository or copy its
example application.

This guide uses the matrix that HyperTodo 1.3.3 runs in CI (typecheck, Jest and
Expo Doctor). Every step below was run end to end on 2026-11-26 against the
[quickstart](quickstart.md) project, in the iOS Simulator through both Expo Go
and a native build: `tsc`, Expo Doctor (all 21 checks), the iOS and Android
exports and the screen-to-fragment tap all worked. A physical device and the
Android emulator have not been tried. It is an example, not a compatibility
range: HyperTodo updates its own pins independently, so read its
`mobile/package.json` for the versions it uses today. See
[Hyperview compatibility](hyperview-compatibility.md) for the schema the package
validates.

| Dependency | Version |
| --- | --- |
| Expo | `~57.0.34` |
| Hyperview | `0.115.1` |
| React | `19.2.3` |
| React Native | `0.86.3` |
| React Navigation (native, native-stack, stack, bottom-tabs) | `7.5.1`, `7.21.0`, `7.12.1`, `7.21.0` |

## 1. Create the application

Use Node 22.19.0, Corepack, and a blank TypeScript project:

```console
nvm install 22.19.0
nvm use 22.19.0
corepack enable
npx create-expo-app@latest hyperview-mobile --template expo-template-blank-typescript@sdk-57 --no-install
cd hyperview-mobile
corepack yarn set version classic
```

The template is pinned to Expo SDK 57. Without the `@sdk-57` tag,
`create-expo-app` now creates an SDK 58 project, and the matrix below would leave
SDK 58 packages behind.

Install the matrix, which includes every peer dependency of Hyperview 0.115.1:

```console
corepack yarn add expo@~57.0.34 hyperview@0.115.1 \
  react@19.2.3 react-native@0.86.3 moment@2.31.0 \
  @react-native-community/datetimepicker@9.1.0 \
  @react-native-picker/picker@2.11.4 \
  @react-navigation/native@7.5.1 @react-navigation/native-stack@7.21.0 \
  @react-navigation/stack@7.12.1 @react-navigation/bottom-tabs@7.21.0 \
  react-native-gesture-handler@~2.32.0 \
  react-native-safe-area-context@~5.7.0 \
  react-native-screens@~4.26.0 react-native-webview@13.16.1
```

The blank TypeScript template already provides `@types/react`, which Hyperview
also lists as a peer. Hyperview ships TypeScript sources that import `lodash` and
`url-parse`, so add their type packages too:

```console
corepack yarn add -D @types/lodash@^4.17.20 @types/url-parse@^1.4.11
```

Yarn does not check native compatibility. Check it with Expo, which should report
every check passed and up-to-date dependencies:

```console
npx expo-doctor
corepack yarn expo install --check
```

## 2. Register the entry point

The template already points `package.json` `main` to `index.ts`. Replace the
contents of `index.ts` with:

```typescript
import "react-native-gesture-handler";
import { registerRootComponent } from "expo";

import App from "./App";

registerRootComponent(App);
```

The gesture-handler import must run before other UI modules.

Hyperview 0.115.1 imports `@react-native/normalize-colors`, which ships no types.
Add the declaration HyperTodo uses at `types/react-native-normalize-colors.d.ts`,
or `tsc` fails inside Hyperview's sources:

```typescript
declare module "@react-native/normalize-colors" {
  export default function normalizeColor(color: unknown): number | null;
}
```

## 3. Create the application shell

Create `.env` in the project root and configure the backend URL through an Expo
public environment variable. This URL is the screen the
[quickstart](quickstart.md) serves:

```dotenv
EXPO_PUBLIC_API_URL=http://127.0.0.1:8000/hyperview/home/
```

Then replace the contents of `App.tsx` with the smallest useful shell. The
`SafeAreaView` keeps the screen below the status bar, where taps reach it:

```tsx
import { NavigationContainer } from "@react-navigation/native";
import Hyperview from "hyperview";
import moment from "moment";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";

const entrypointUrl = process.env.EXPO_PUBLIC_API_URL;

if (!entrypointUrl) {
  throw new Error("EXPO_PUBLIC_API_URL is required");
}

export default function App() {
  return (
    <SafeAreaProvider>
      <SafeAreaView style={{ flex: 1 }}>
        <NavigationContainer>
          <Hyperview
            entrypointUrl={entrypointUrl}
            fetch={fetch}
            formatDate={(date, format) =>
              date && format ? moment(date).format(format) : undefined
            }
          />
        </NavigationContainer>
      </SafeAreaView>
    </SafeAreaProvider>
  );
}
```

Start without custom components or behaviors. Add native extensions only after
a tested interaction cannot be expressed in HXML.

## 4. Select a reachable Django URL

The URL depends on where the client runs:

| Client | Backend host |
| --- | --- |
| iOS Simulator | `127.0.0.1` |
| Android Emulator | `10.0.2.2` |
| Physical device | Development machine LAN address |

For a physical device, use the machine's reachable LAN address and configure
Django `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` for it. Prefer binding Django to
that single address (`runserver 192.168.1.20:8000`); bind `0.0.0.0` only on a
trusted development network, with those settings limited to the addresses you
use, and never in a deployed environment. The phone and development machine must
be on the same reachable network. The [SSE guide](realtime.md) applies the same
rule.

## 5. Run through Expo Go

Start Metro from the mobile project:

```console
corepack yarn expo start --go
```

Scan the QR code with the phone camera and open it in Expo Go. On the iOS
Simulator, `corepack yarn expo start --go --ios` installs Expo Go and opens the
project without a QR code. JavaScript, TypeScript, and server-rendered HXML
changes do not require a native rebuild.

This flow needs no Expo account. If you sign the project, Expo Go and Expo CLI
must use the same account; check or establish the CLI session with:

```console
corepack yarn expo whoami
corepack yarn expo login
```

For an Expo account created through Google, finish browser authentication with
**Continue with Google**. Do not enter the Google password in the terminal.

Create a development build only when the application adds native dependencies
or native configuration not bundled in Expo Go. For the iOS Simulator,
`corepack yarn expo run:ios` builds and launches one; it needs Xcode and
CocoaPods.

## Troubleshoot update responses

Hyperview navigation can load a complete HXML document with
`application/vnd.hyperview+xml`. The `replace`, `append`, and `prepend` actions
need a bare element with `application/vnd.hyperview_fragment+xml`.

`XMLRestrictedElementFound` usually means an update endpoint returned a full
`doc`, `navigator`, `screen`, or `body` wrapper. Use
`HyperviewFragmentTemplateResponse`, return only the target element, and give
that element a stable ID. See [HTTP responses](http-responses.md) for the server
contract.

