import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Placeholder for auth-gating once we have a real protected surface.
// For now this just passes everything through, the auth pages are public
// and there are no protected routes yet.
export function middleware(_request: NextRequest) {
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon|brand|apple-touch-icon).*)"],
};
