/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  transpilePackages: [
    "@magpie/api-utils",
    "@magpie/auth",
    "@magpie/tailwind-config",
    "@magpie/ui",
  ],
  // Defensive security headers. The device-authorize page is the
  // canonical clickjacking target (an iframe could trick a logged-in
  // user into authorizing an attacker's CLI session), so we apply
  // anti-frame headers globally — none of our pages are meant to be
  // embedded.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          // Modern browsers: definitive anti-iframe.
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
          // Legacy fallback (older browsers without CSP frame-ancestors).
          { key: "X-Frame-Options", value: "DENY" },
          // Block MIME-sniffing-based content-type confusion.
          { key: "X-Content-Type-Options", value: "nosniff" },
          // Don't leak full URLs to cross-origin links / referrers.
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
