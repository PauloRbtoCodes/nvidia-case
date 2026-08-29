/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    // A API roda em :8000. O proxy evita CORS e mantém o front agnóstico de host:
    // trocar o backend é mudar RADAR_API_URL, não recompilar o cliente.
    return [
      { source: "/api/:path*", destination: `${process.env.RADAR_API_URL ?? "http://localhost:8000"}/:path*` },
    ];
  },
};
export default nextConfig;
