import { initializeApp, getApps } from "firebase/app";
import { getAuth, connectAuthEmulator, GoogleAuthProvider } from "firebase/auth";

const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY!,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN!,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID!,
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET!,
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID!,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID!,
};

const app = getApps().length === 0 ? initializeApp(firebaseConfig) : getApps()[0];

export const auth = getAuth(app);

// E2E テスト・ローカル開発用: env が設定されているときだけ Auth エミュレータへ接続する。
// 本番ビルドではこの env を設定しないため挙動は変わらない。
if (process.env.NEXT_PUBLIC_AUTH_EMULATOR_HOST) {
  connectAuthEmulator(auth, `http://${process.env.NEXT_PUBLIC_AUTH_EMULATOR_HOST}`, {
    disableWarnings: true,
  });
}

export const googleProvider = new GoogleAuthProvider();
