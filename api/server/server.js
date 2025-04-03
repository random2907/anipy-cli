import express from "express";
import axios from "axios";
import { webcrack } from "webcrack";

const app = express();
app.use(express.json());

function find_all_functions(jsCode, functionPattern) {
  const matches = [];
  const regex = new RegExp(functionPattern, "g");

  let match;
  while ((match = regex.exec(jsCode)) !== null) {
    let startIdx = match.index;
    let bracketCount = 0;
    let endIdx = startIdx;

    while (jsCode[endIdx] !== "{" && endIdx < jsCode.length) {
      endIdx++;
    }

    if (jsCode[endIdx] === "{") bracketCount++;
    while (bracketCount > 0 && endIdx < jsCode.length) {
      endIdx++;
      if (jsCode[endIdx] === "{") bracketCount++;
      if (jsCode[endIdx] === "}") bracketCount--;
    }

    matches.push(jsCode.slice(startIdx, endIdx + 1));
  }
  return matches.length ? matches : null;
}

app.post("/deobfuscate", async (req, res) => {
  try {
    const { js_url } = req.body;
    if (!js_url)
      return res.status(400).json({ error: "Missing 'js_url' parameter" });

    console.log(`Fetching JavaScript from: ${js_url}`);
    const response = await axios.get(js_url);
    const js_code = response.data;

    console.log("Running WebCrack...");
    const data = await webcrack(js_code);
    const result = data.code;

    console.log("✅ Extracting function...");
    const extractedCode = find_all_functions(result, new RegExp("5: \\[function \\(n, t, r\\) \\{", "g"));
    if (!extractedCode) {
      return res.status(404).json({ error: "Function block not found" });
    }

    const extractedFunctions = find_all_functions(extractedCode[0], /function\s+[^\s]+\(\s*n\s*\)\s*\{/g);
    let decodeFunction;
        for (let i=0; i<extractedFunctions.length; i++){
                        if(extractedFunctions[i].includes("decodeURIComponent")){
                                decodeFunction = extractedFunctions[i];
                                break;
                        }
        }
    res.json({ result: extractedCode, function: extractedFunctions, decode: decodeFunction || "Not Found" });
  } catch (error) {
    console.error("❌ Error:", error.message);
    res.status(500).json({ error: "Failed to process JavaScript" });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`🚀 Server running on port ${PORT}`));

