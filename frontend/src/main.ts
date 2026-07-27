import { mount } from "svelte";
import "@fontsource-variable/inter";
import "@fontsource-variable/inter/wght-italic.css";
import App from "./App.svelte";
import "./app.css";

const app = mount(App, {
  target: document.getElementById("app")!,
});

export default app;
