import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import { Toaster } from "sonner";
import { ErrorBoundary } from "./components/common/ErrorBoundary";
import { router } from "./router";
import { syncLlmFromBackend } from "./lib/llm";
import "./index.css";

// 启动时从后端拉一次 AI 配置刷入本地缓存：后端为主，让本机/多端配置一致。
syncLlmFromBackend();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <RouterProvider router={router} />
      <Toaster position="bottom-right" theme="dark" richColors closeButton duration={3500} />
    </ErrorBoundary>
  </StrictMode>
);
