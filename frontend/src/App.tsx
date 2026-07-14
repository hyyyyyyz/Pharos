import { Landing } from "./components/Landing";
import { Workbench } from "./components/Workbench";
import { useUI } from "./store";
import "./App.css";

export default function App() {
  const view = useUI((s) => s.view);
  return view === "landing" ? <Landing /> : <Workbench />;
}
