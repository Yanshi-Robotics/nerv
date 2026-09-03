import "./globals.css";
import type { Metadata } from "next";
import type { ReactNode } from "react";

import { LOCALES, SOURCE_LANG } from "@/lib/locales.generated";

export const metadata: Metadata = {
  title: "NERV",
  description: "NERV — operator console: a session is brain × body × world",
};

// 首屏绘制前就把主题定好：读 localStorage 的偏好，没存过就用深色（默认）。
// 这样刷新带浅色偏好的页面时，不会先闪一下深色再变浅。
const THEME_INIT = `(function(){try{var t=localStorage.getItem('nerv-theme');if(t!=='light'&&t!=='dark')t='dark';document.documentElement.dataset.theme=t;}catch(e){document.documentElement.dataset.theme='dark';}})();`;

// 同样在首屏绘制前把 <html lang> 定好。
//
// ⚠️ 这里修的是一个**一直存在的**毛病：`setLang()` 是全仓唯一改 `documentElement.lang` 的地方，
//    所以用户不去点那个切换器，这个属性就永远停在预渲染时的值——中文用户读到的页面对外一直
//    声称自己是英文。屏幕阅读器和翻译工具都看这个属性。
//
// ⛔ 语言代码 → html 标签的映射**从各语言文件的 meta.html 生成**，不在这里写映射表：
//    加一门语言时这里不该是需要改的地方。
const LANG_TAGS = JSON.stringify(
  Object.fromEntries(Object.values(LOCALES).map((l) => [l.meta.code, l.meta.html])),
);
const LANG_INIT = `(function(){try{var m=${LANG_TAGS},s=localStorage.getItem('nerv-lang');if(s&&m[s]){document.documentElement.lang=m[s];return;}var n=(navigator.language||'').toLowerCase(),b='',bl=0;for(var k in m){var p=k.toLowerCase();if(n.indexOf(p)===0&&p.length>bl){b=m[k];bl=p.length;}}if(b)document.documentElement.lang=b;}catch(e){}})();`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // 预渲染用正典语言——静态导出的这份 HTML 里的文案就是正典语言的文案，两者必须一致。
    // 上一版这里写死 `lang="zh"` 而内容是英文，对外声称的和实际给的对不上。
    <html lang={LOCALES[SOURCE_LANG].meta.html} data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
        <script dangerouslySetInnerHTML={{ __html: LANG_INIT }} />
      </head>
      <body className="antialiased">{children}</body>
    </html>
  );
}
