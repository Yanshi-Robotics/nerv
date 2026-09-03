/** @type {import('next').NextConfig} */

// Two build modes, one codebase.
//
//   npm run dev / build     the development server, talking to a backend on :8000
//   npm run build:static    a folder of files with no server behind them, which the
//                           Python package carries so `nerv serve` can hand it out
//
// The static mode is what lets `pip install nerv` produce a working web interface with no
// node on the machine at all. It is gated behind an env var rather than being the default
// because `output: "export"` silently disables anything that needs a server, and having
// that happen during ordinary development would be confusing.
//
// 两种构建模式，一套代码。
//
//   npm run dev / build     开发服务器，连 :8000 的后端
//   npm run build:static    一堆背后没有服务器的静态文件，由 Python 包带着走，
//                           好让 `nerv serve` 自己把它端出去
//
// 静态模式是「`pip install nerv` 之后机器上没有 node 也有网页可用」的前提。它由环境变量把关、
// 不做默认，是因为 `output: "export"` 会**静默**停掉一切需要服务器的东西——让这种事在日常开发里
// 发生，会让人摸不着头脑。
const isStaticExport = process.env.NERV_STATIC_EXPORT === "1";

const nextConfig = {
  // 关掉 Next 开发模式注入的 dev 工具指示器（默认在左下角那个会挡内容、又不能拖的「N」按钮）。
  // 只影响开发时的那个浮标，不动任何功能。左下角腾给我们自己的主题切换。
  devIndicators: false,

  ...(isStaticExport ? { output: "export" } : {}),

  // 每个路由导出成 <route>/index.html 而不是 <route>.html。
  // 后端用 Starlette 的 StaticFiles(html=True) 端这个目录，它只认「目录 + index.html」：
  // 没有这一项时 /nerv、/session-logs 在 `nerv serve` 下全是 404（2026-09-02 实测）。
  // 开发模式也开着，两种模式行为一致。
  trailingSlash: true,

  // v0.6 的 /anima-logs → /session-logs 永久跳转**不再写在这里**。
  // `output: "export"` 不支持 redirects()——静态文件背后没有服务器可以发 308。
  // 那条链接是承诺过的，所以它没有被删掉，而是改成了 app/anima-logs/page.tsx 的客户端跳转：
  // 两种模式下行为一致，也就不会出现「开发时能跳、装出来的包里跳不了」这种只在发布之后才被发现的差异。
};

export default nextConfig;
