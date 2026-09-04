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
      { source: '/coverage', destination: `${miner}/coverage` },
      { source: '/operator/:path*', destination: `${miner}/operator/:path*` },
      { source: '/operators/public', destination: `${miner}/operators/public` },
      { source: '/market/:path*', destination: `${miner}/market/:path*` },
      { source: '/player/:path*', destination: `${miner}/player/:path*` },
      { source: '/players/:path*', destination: `${miner}/players/:path*` },
      { source: '/attribution/:path*', destination: `${miner}/attribution/:path*` },
      { source: '/health/:path*', destination: `${miner}/health/:path*` },
    ];
  },
};

export default nextConfig;
