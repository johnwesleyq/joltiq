/** API is proxied same-origin so the session cookie is first-party (SameSite=Lax, HttpOnly) and no CORS is needed. */
const API = process.env.JOLTIQ_API_URL || "http://127.0.0.1:8000";
const csp = [
  "default-src 'self'", "script-src 'self' 'unsafe-inline'", "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' https://fonts.gstatic.com", "img-src 'self' data: blob: https:", "connect-src 'self'", "frame-ancestors 'none'",
  "base-uri 'self'", "form-action 'self'",
].join("; ");
export default {
  reactStrictMode: true,
  poweredByHeader: false,
  output: "standalone",
  async rewrites() { return [{ source: "/api/:path*", destination: `${API}/api/:path*` }]; },
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "Content-Security-Policy", value: csp }, { key: "X-Frame-Options", value: "DENY" },
      { key: "X-Content-Type-Options", value: "nosniff" }, { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      { key: "Permissions-Policy", value: "camera=(), geolocation=(), microphone=(self)" },
    ] }];
  },
};
