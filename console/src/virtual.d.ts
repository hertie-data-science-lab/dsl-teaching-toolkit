// Modules that vite.config.ts serves.

declare module 'virtual:validators' {
  /** JSON.stringify(schema) -> its precompiled validator. */
  export const table: Record<string, (data: unknown) => boolean>;
}
