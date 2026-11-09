import {defineConfig} from "@playwright/test";
export default defineConfig({testDir:"./tests",use:{headless:true,launchOptions:{executablePath:"/usr/bin/chromium",args:["--no-sandbox"]}},timeout:30000});
