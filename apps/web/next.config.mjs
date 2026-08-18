/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  transpilePackages: ['@degenlens/shared'],
  async rewrites() {
    const miner = process.env.LOCAL_MINER_URL ?? 'http://127.0.0.1:8787';
    return [
      {
        source: '/health',
        destination: `${miner}/health`,
      },
      {
        source: '/metrics',
        destination: `${miner}/metrics`,
      },
      {
        source: '/meta',
        destination: `${miner}/meta`,
      },
      {
        source: '/docs/:path*',
        destination: `${miner}/docs/:path*`,
      },
      {
        source: '/openapi.json',
        destination: `${miner}/openapi.json`,
      },
      {
        source: '/casinos',
        destination: `${miner}/casinos`,
      },
      {
        source: '/casino/:path*',
        destination: `${miner}/casino/:path*`,
      },
      {
        source: '/wallet/:path*',
        destination: `${miner}/wallet/:path*`,
      },
      {
        source: '/anomaly/:path*',
        destination: `${miner}/anomaly/:path*`,
      },
      {
        source: '/transaction/:path*',
        destination: `${miner}/transaction/:path*`,
      },
    ];
  },
};

export default nextConfig;
