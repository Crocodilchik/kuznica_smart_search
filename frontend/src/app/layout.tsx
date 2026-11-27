import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Умный поиск Softline",
  description: "Поиск IT-товаров по описанию задачи, категории или артикулу.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
