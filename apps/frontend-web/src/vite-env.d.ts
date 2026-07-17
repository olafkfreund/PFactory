/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly DEV: boolean;
  readonly PROD: boolean;
  readonly MODE: string;
  // Remote access configuration
  readonly VITE_API_BASE_URL?: string;  // e.g., "http://your-server.example.com:3114/api"
  readonly VITE_WS_BASE_URL?: string;   // e.g., "ws://your-server.example.com:3114"
  readonly VITE_API_URL?: string;       // Backend URL for Vite proxy (dev only)
  readonly VITE_WS_URL?: string;        // WebSocket URL for Vite proxy (dev only)
  // Sibling portal hosts for the portal switcher (default to the live portals)
  readonly VITE_AIFACTORY_URL?: string;
  readonly VITE_TFACTORY_URL?: string;
  readonly VITE_CFACTORY_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
