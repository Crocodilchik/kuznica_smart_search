import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Softline Store — интернет-магазин программного обеспечения и электроники",
  description: "Лицензионное программное обеспечение, электроника и умный подбор IT-товаров для бизнеса и дома.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
