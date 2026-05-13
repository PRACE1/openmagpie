import type { Metadata } from "next";
import { DeviceAuthPage } from "./_components/device-auth-page";

export const metadata: Metadata = {
  title: "Authorize CLI | OpenMagpie",
};

export default async function DeviceAuthorizeRoute({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = await params;
  return <DeviceAuthPage sessionId={sessionId} />;
}
