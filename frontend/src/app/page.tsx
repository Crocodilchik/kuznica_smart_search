import uiConfig from "../../../smart_search/interfaces/ui_config.json";
import SearchExperience from "@/components/search-experience";

export default function HomePage() {
  return <SearchExperience config={uiConfig} />;
}
