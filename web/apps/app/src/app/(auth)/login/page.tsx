import type { Metadata } from "next";
import { LoginForm } from "./_components/login-form";

export const metadata: Metadata = {
  title: "Sign in | OpenMagpie",
};

export default function LoginPage() {
  return <LoginForm />;
}
