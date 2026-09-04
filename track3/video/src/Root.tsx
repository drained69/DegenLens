import {Composition} from "remotion";
import {DegenLensExplainer} from "./Video";

export const RemotionRoot = () => (
  <Composition
    id="DegenLensExplainer"
    component={DegenLensExplainer}
    durationInFrames={3450}
    fps={30}
    width={1920}
    height={1080}
  />
);
