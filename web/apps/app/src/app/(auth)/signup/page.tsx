import type { Metadata } from "next";
import { SignupForm } from "./_components/signup-form";

export const metadata: Metadata = {
  title: "Sign up | OpenMagpie",
};

export default function SignupPage() {
  return <SignupForm />;
}
