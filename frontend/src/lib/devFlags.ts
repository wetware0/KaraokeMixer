export function isFakeRecipeEnabled(): boolean {
  return import.meta.env.VITE_ENABLE_FAKE_RECIPE === "true";
}
