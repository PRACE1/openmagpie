import type { Metadata } from "next";
import { LogoutPage } from "./_components/logout-page";

export const metadata: Metadata = {
  title: "Signing out | OpenMagpie",
};

export default function Page() {
  return <LogoutPage />;
}
