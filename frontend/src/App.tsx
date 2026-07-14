import { useEffect } from "react";
import { Landing } from "./components/Landing";
import { Workbench } from "./components/Workbench";
import { useUI } from "./store";
import "./App.css";

export default function App() {
  const view = useUI((s) => s.view);
  const mode = useUI((s) => s.mode);
  const accent = useUI((s) => s.accent);

  useEffect(() => {
    document.documentElement.dataset.theme = mode;
    localStorage.setItem("ph-mode", mode);
  }, [mode]);
  useEffect(() => {
    document.documentElement.dataset.accent = accent;
    localStorage.setItem("ph-accent", accent);
  }, [accent]);

  return view === "landing" ? <Landing /> : <Workbench />;
}
