# Create a Hyperview mobile client

Start with a normal blank Expo application and install Hyperview as a
dependency. You do not need to clone the Hyperview repository or copy its
example application.

This guide records the reference matrix validated with dj-hyperview:

| Dependency | Tested version |
| --- | --- |
| Expo | `~57.0.20` |
| Hyperview | `0.110.0` |
| React | `19.2.3` |
| React Native | `0.86.3` |

## 1. Create the application

Use Node 22.19.0, Corepack, and a blank TypeScript project:

```console
nvm install 22.19.0
nvm use 22.19.0
corepack enable
npx create-expo-app@latest hyperview-mobile --template blank-typescript --no-install
cd hyperview-mobile
corepack yarn set version classic
```

Install the tested runtime matrix:

```console
corepack yarn add expo@~57.0.20 hyperview@0.110.0 \
  react@19.2.3 react-native@0.86.3 moment@2.30.1 \
  @react-native-community/datetimepicker@9.1.0 \
  @react-native-picker/picker@2.11.4 \
  @react-navigation/native@6.1.6 @react-navigation/stack@6.3.16 \
  @react-navigation/bottom-tabs@6.5.7 \
  react-native-gesture-handler@~2.32.0 \
  react-native-safe-area-context@~5.7.0 \
  react-native-screens@~4.26.0 react-native-webview@13.16.1
```

Hyperview declares peer ranges from an older example stack, so Yarn can report
peer warnings for this tested Expo matrix. Use Expo Doctor and device testing as
the compatibility gates.

## 2. Register the entry point

Create `index.ts` and set `package.json` `main` to `index.ts`:

```typescript
import "react-native-gesture-handler";
import { registerRootComponent } from "expo";

import App from "./App";

registerRootComponent(App);
```

The gesture-handler import must run before other UI modules.

## 3. Create the application shell

Configure the backend URL through an Expo public environment variable:

```dotenv
EXPO_PUBLIC_API_URL=http://127.0.0.1:8000/hv/
```

Then create the smallest useful shell:

```tsx
import { NavigationContainer } from "@react-navigation/native";
import Hyperview from "hyperview";
import moment from "moment";
import { SafeAreaProvider } from "react-native-safe-area-context";

const entrypointUrl = process.env.EXPO_PUBLIC_API_URL;

if (!entrypointUrl) {
  throw new Error("EXPO_PUBLIC_API_URL is required");
}

export default function App() {
  return (
    <SafeAreaProvider>
      <NavigationContainer>
        <Hyperview
          entrypointUrl={entrypointUrl}
          formatDate={(date, format) =>
            date && format ? moment(date).format(format) : undefined
          }
        />
      </NavigationContainer>
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

For a physical device, bind Django to `0.0.0.0`, use the machine's reachable
LAN address, and configure Django `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`.
The phone and development machine must be on the same reachable network.

## 5. Run through Expo Go

Start Metro from the mobile project:

```console
corepack yarn expo start --go
```

Scan the QR code with the phone camera and open it in Expo Go. JavaScript,
TypeScript, and server-rendered HXML changes do not require a native rebuild.

Expo Go and Expo CLI must use the same Expo account when the project is signed.
Check or establish the CLI session with:

```console
corepack yarn expo whoami
corepack yarn expo login
```

For an Expo account created through Google, finish browser authentication with
**Continue with Google**. Do not enter the Google password in the terminal.

Create a development build only when the application adds native dependencies
or native configuration not bundled in Expo Go.

## Troubleshoot update responses

Hyperview navigation can load a complete HXML document with
`application/vnd.hyperview+xml`. The `replace`, `append`, and `prepend` actions
need a bare element with `application/vnd.hyperview_fragment+xml`.

`XMLRestrictedElementFound` usually means an update endpoint returned a full
`doc`, `navigator`, `screen`, or `body` wrapper. Use
`HyperviewFragmentTemplateResponse`, return only the target element, and give
that element a stable ID. See [HTTP responses](http-responses.md) for the server
contract.

