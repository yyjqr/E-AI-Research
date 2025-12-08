import os
import sys
import re
import time
import json
import shutil
import subprocess
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup

# --- Configuration & Constants ---
CONFIG_FILE = "hunter_config.json"
LOG_FILE = "hunter_execution.log"

class Logger:
    @staticmethod
    def log(message):
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{ts}] {message}")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")

class ConfigManager:
    @staticmethod
    def load_config():
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        return {
            "sources": [
                "https://github.com/ashishpatel26/500-AI-Agents-Projects",
                "https://github.com/e2b-dev/awesome-ai-agents",
                "https://github.com/Hannibal046/Awesome-LLM",
                "https://github.com/steven2358/awesome-generative-ai"
            ],
            "keywords": ["iot", "robotics", "automation", "agent", "autonomous"],
            "min_stars": 200,
            "max_repos_to_process": 5,
            "workspace_dir": "hunter_workspace",
            "use_github_api": True
        }

class NetworkUtils:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

    def fetch_content(self, url):
        """Try requests, fallback to curl."""
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            Logger.log(f"[Net] Requests failed for {url}: {e}")
        
        # Fallback to curl
        try:
            res = subprocess.run(["curl", "-L", "-s", url], capture_output=True, timeout=20)
            if res.returncode == 0 and res.stdout:
                return res.stdout.decode('utf-8', errors='ignore')
        except Exception as e:
            Logger.log(f"[Net] Curl failed for {url}: {e}")
        
        return None
    
    def fetch_json(self, url, params=None):
        """Fetch JSON data (for API)."""
        try:
            resp = self.session.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            else:
                # Logger.log(f"[Net] API Error {resp.status_code}: {resp.text}")
                pass
        except Exception as e:
            Logger.log(f"[Net] API Request failed: {e}")
        return None

class GitHubSearchCollector:
    """Uses GitHub Search API to find repositories."""
    def __init__(self, net_utils, config):
        self.net = net_utils
        self.keywords = config.get("keywords", [])
        self.min_stars = config.get("min_stars", 50)
        self.api_url = "https://api.github.com/search/repositories"

    def collect_candidates(self):
        candidates = {} # {url: source_description}
        Logger.log("[API Collector] Starting GitHub API Search...")
        
        for kw in self.keywords:
            # Construct query: keyword + language:python + stars constraint
            query = f"{kw} stars:>{self.min_stars}"
            params = {
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": 5  # Limit per keyword to avoid rate limits
            }
            
            Logger.log(f"[API Collector] Searching for: '{query}'")
            data = self.net.fetch_json(self.api_url, params)
            
            if data and "items" in data:
                for item in data["items"]:
                    html_url = item.get("html_url")
                    if html_url:
                        candidates[html_url] = f"GitHub API (Query: {kw})"
            
            # Sleep briefly to respect unauthenticated rate limits (10 req/min)
            time.sleep(2)
            
        Logger.log(f"[API Collector] Found {len(candidates)} candidates via API.")
        return candidates

class SourceCollector:
    """Scrapes specific 'Awesome' lists or pages."""
    def __init__(self, net_utils, config):
        self.net = net_utils
        self.sources = config.get("sources", [])

    def collect_candidates(self):
        candidates = {} # {url: source_url}
        for source in self.sources:
            Logger.log(f"[Page Collector] Scraping source: {source}")
            content = self.net.fetch_content(source)
            if not content:
                if "github.com" in source and "blob" in source:
                    raw_url = source.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
                    Logger.log(f"[Page Collector] Trying raw URL: {raw_url}")
                    content = self.net.fetch_content(raw_url)
            
            if content:
                matches = re.findall(r'github\.com/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_]+)', content)
                for user, repo in matches:
                    if user.lower() not in ['topics', 'site', 'features', 'about', 'contact', 'pricing', 'sponsors', 'login', 'join']:
                        full_url = f"https://github.com/{user}/{repo}"
                        if full_url not in candidates:
                            candidates[full_url] = source
        
        Logger.log(f"[Page Collector] Found {len(candidates)} unique candidates.")
        return candidates

class RepoAnalyzer:
    def __init__(self, net_utils, config):
        self.net = net_utils
        self.keywords = [k.lower() for k in config.get("keywords", [])]
        self.min_stars = config.get("min_stars", 0)
        self.use_api = config.get("use_github_api", False)

    def analyze(self, repo_url):
        """Returns dict of metadata if repo is valuable, else None."""
        
        # Try to get metadata via API first if enabled (more accurate dates)
        api_data = None
        if self.use_api:
            parts = urlparse(repo_url).path.strip("/").split("/")
            if len(parts) >= 2:
                api_url = f"https://api.github.com/repos/{parts[0]}/{parts[1]}"
                api_data = self.net.fetch_json(api_url)

        content = self.net.fetch_content(repo_url)
        if not content:
            return None
        
        soup = BeautifulSoup(content, 'html.parser')
        
        # 1. Extract Stars
        stars = 0
        if api_data:
            stars = api_data.get("stargazers_count", 0)
        else:
            try:
                star_elem = soup.select_one('span#repo-stars-counter-star')
                if not star_elem:
                    star_elem = soup.select_one('.social-count')
                
                if star_elem:
                    txt = star_elem.get_text(strip=True).replace(',', '')
                    if 'k' in txt:
                        stars = int(float(txt.replace('k', '')) * 1000)
                    else:
                        stars = int(txt)
            except:
                pass

        if stars < self.min_stars:
            return None

        # 2. Extract Description & Topics
        desc = api_data.get("description", "") if api_data else ""
        if not desc:
            desc_elem = soup.select_one('p.f4.my-3')
            if desc_elem:
                desc = desc_elem.get_text(strip=True)
        
        topics = api_data.get("topics", []) if api_data else []
        if not topics:
            for t in soup.select('a.topic-tag'):
                topics.append(t.get_text(strip=True))
        
        # 3. Extract Dates
        created_at = "Unknown"
        updated_at = "Unknown"
        
        if api_data:
            created_at = api_data.get("created_at", "Unknown")
            updated_at = api_data.get("updated_at", "Unknown")
        else:
            # Try to find relative-time in HTML
            rel_times = soup.select('relative-time')
            if rel_times:
                # Usually the first one in file list header is latest commit
                updated_at = rel_times[0].get('datetime', 'Unknown')

        # 4. Calculate Relevance Score
        full_text = (str(desc) + " " + " ".join(topics) + " " + repo_url).lower()
        score = 0
        matched = []
        for kw in self.keywords:
            if kw in full_text:
                score += 1
                matched.append(kw)
        
        if score == 0 and stars < 2000:
            return None
        
        final_score = score * 10 + (stars / 1000.0)
        
        return {
            "url": repo_url,
            "name": repo_url.split('/')[-1],
            "stars": stars,
            "description": desc,
            "keywords": matched,
            "topics": topics,
            "created_at": created_at,
            "updated_at": updated_at,
            "score": final_score
        }

class BuilderRunner:
    def __init__(self, workspace_dir):
        self.workspace_dir = workspace_dir
        self.venv_dir = os.path.join(workspace_dir, "venv")
        self.setup_venv()

    def setup_venv(self):
        if not os.path.exists(self.venv_dir):
            Logger.log(f"[Env] Creating venv in {self.venv_dir}")
            subprocess.run([sys.executable, "-m", "venv", self.venv_dir], check=True)
        self.python_bin = os.path.join(self.venv_dir, "bin", "python")
        self.pip_bin = os.path.join(self.venv_dir, "bin", "pip")

    def process_repo(self, repo_data):
        repo_name = repo_data['name']
        target_dir = os.path.join(self.workspace_dir, repo_name)
        
        Logger.log(f"--- Processing {repo_name} (Score: {repo_data['score']:.1f}) ---")
        Logger.log(f"    URL: {repo_data['url']}")
        
        # 1. Clone or Update
        repo_ready = False
        if os.path.exists(target_dir) and os.path.exists(os.path.join(target_dir, ".git")):
            Logger.log("    [Git] Repository exists. Updating...")
            try:
                subprocess.run(["git", "pull"], cwd=target_dir, check=True, capture_output=True, timeout=60)
                Logger.log("    [Git] Update Success")
                repo_ready = True
            except Exception:
                Logger.log("    [Git] Update Failed. Re-cloning...")
                shutil.rmtree(target_dir)
        
        if not repo_ready:
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir)
            if not self._clone_repo(repo_data['url'], target_dir):
                return

        # 2. Detect Language & Build
        languages = self._detect_languages(target_dir)
        Logger.log(f"    [Lang] Detected: {', '.join(languages)}")

        if "C++" in languages or "C" in languages:
            self._build_and_run_cpp(target_dir)
        
        if "Python" in languages:
            self._install_python_deps(target_dir)
            self._attempt_run_python(target_dir)

    def _clone_repo(self, url, target_dir):
        try:
            subprocess.run(["git", "clone", "--depth", "1", url, target_dir], 
                           check=True, capture_output=True)
            Logger.log("    [Clone] Success")
            return True
        except subprocess.CalledProcessError:
            Logger.log("    [Clone] Failed")
            return False

    def _detect_languages(self, target_dir):
        langs = set()
        for root, dirs, files in os.walk(target_dir):
            if ".git" in dirs: dirs.remove(".git")
            for f in files:
                if f.endswith(".py"): langs.add("Python")
                if f.endswith(".cpp") or f.endswith(".cc") or f.endswith(".hpp"): langs.add("C++")
                if f.endswith(".c") or f.endswith(".h"): langs.add("C")
                if f == "CMakeLists.txt": langs.add("C++") # Strong indicator
                if f == "Makefile": langs.add("C") # Could be C or C++
        return list(langs)

    # --- C/C++ Logic ---
    def _build_and_run_cpp(self, target_dir):
        Logger.log("    [Build] Starting C/C++ Build...")
        build_success = False
        
        # Method A: CMake
        if os.path.exists(os.path.join(target_dir, "CMakeLists.txt")):
            Logger.log("    [Build] Found CMakeLists.txt")
            build_dir = os.path.join(target_dir, "build")
            os.makedirs(build_dir, exist_ok=True)
            try:
                subprocess.run(["cmake", ".."], cwd=build_dir, check=True, capture_output=True)
                subprocess.run(["make"], cwd=build_dir, check=True, capture_output=True)
                Logger.log("    [Build] CMake Build Success")
                build_success = True
                self._find_and_run_binary(build_dir)
            except subprocess.CalledProcessError as e:
                Logger.log(f"    [Build] CMake Build Failed: {e.stderr.decode('utf-8', errors='ignore') if e.stderr else 'Unknown error'}")

        # Method B: Makefile
        elif os.path.exists(os.path.join(target_dir, "Makefile")):
            Logger.log("    [Build] Found Makefile")
            try:
                subprocess.run(["make"], cwd=target_dir, check=True, capture_output=True)
                Logger.log("    [Build] Make Success")
                build_success = True
                self._find_and_run_binary(target_dir)
            except subprocess.CalledProcessError as e:
                Logger.log(f"    [Build] Make Failed: {e.stderr.decode('utf-8', errors='ignore') if e.stderr else 'Unknown error'}")
        
        # Method C: Simple Compile (Fallback)
        else:
            Logger.log("    [Build] No build system found. Trying to compile main file...")
            main_file = None
            for f in os.listdir(target_dir):
                if f.lower() in ["main.cpp", "main.c", "app.cpp", "test.cpp"]:
                    main_file = f
                    break
            
            if main_file:
                compiler = "g++" if main_file.endswith("cpp") else "gcc"
                out_bin = os.path.join(target_dir, "a.out")
                try:
                    subprocess.run([compiler, main_file, "-o", out_bin], cwd=target_dir, check=True, capture_output=True)
                    Logger.log(f"    [Build] Compiled {main_file} successfully")
                    build_success = True
                    self._run_binary(out_bin)
                except subprocess.CalledProcessError as e:
                    Logger.log(f"    [Build] Compilation Failed: {e.stderr.decode('utf-8', errors='ignore')}")
            else:
                Logger.log("    [Build] No main file found to compile.")

    def _find_and_run_binary(self, search_dir):
        # Heuristic: Find newest executable file
        candidates = []
        for root, dirs, files in os.walk(search_dir):
            for f in files:
                path = os.path.join(root, f)
                if os.access(path, os.X_OK) and not f.endswith(".sh") and not f.endswith(".py") and not os.path.isdir(path):
                    candidates.append(path)
        
        if candidates:
            # Sort by modification time
            candidates.sort(key=os.path.getmtime, reverse=True)
            target = candidates[0]
            self._run_binary(target)
        else:
            Logger.log("    [Run] No executable binary found after build.")

    def _run_binary(self, binary_path):
        Logger.log(f"    [Run] Executing {os.path.basename(binary_path)}...")
        try:
            proc = subprocess.Popen([binary_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                out, err = proc.communicate(timeout=5)
                Logger.log(f"    [Run] Output:\n{out.decode('utf-8', errors='ignore')}")
            except subprocess.TimeoutExpired:
                proc.kill()
                Logger.log("    [Run] Process ran for 5s (Service/Long-running).")
        except Exception as e:
            Logger.log(f"    [Run] Execution failed: {e}")

    # --- Python Logic ---
    def _install_python_deps(self, target_dir):
        # A. requirements.txt
        if os.path.exists(os.path.join(target_dir, "requirements.txt")):
            Logger.log("    [Install] Found requirements.txt")
            try:
                subprocess.run([self.pip_bin, "install", "-r", "requirements.txt"], 
                               cwd=target_dir, capture_output=True, timeout=120)
                Logger.log("    [Install] requirements.txt installed")
            except Exception as e:
                Logger.log(f"    [Install] requirements.txt failed: {e}")

        # B. setup.py
        if os.path.exists(os.path.join(target_dir, "setup.py")):
            Logger.log("    [Install] Found setup.py")
            try:
                subprocess.run([self.pip_bin, "install", "."], 
                               cwd=target_dir, capture_output=True, timeout=120)
            except Exception:
                pass

    def _attempt_run_python(self, target_dir):
        candidates = ["main.py", "app.py", "demo.py", "test.py", "agent.py", "run.py"]
        ran_something = False
        for f in os.listdir(target_dir):
            if f in candidates:
                Logger.log(f"    [Run] Attempting to run {f}...")
                try:
                    proc = subprocess.Popen([self.python_bin, f], cwd=target_dir, 
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    try:
                        out, err = proc.communicate(timeout=5)
                        Logger.log(f"    [Run] {f} finished (Exit: {proc.returncode})")
                        if out: Logger.log(f"    [Output] {out.decode('utf-8', errors='ignore')[:200]}...")
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        Logger.log(f"    [Run] {f} ran for 5s (likely a service/loop). Success.")
                    
                    ran_something = True
                    break 
                except Exception as e:
                    Logger.log(f"    [Run] Error running {f}: {e}")
        
        if not ran_something:
            Logger.log("    [Run] No obvious Python entry point found.")

class AgentHunter:
    def __init__(self):
        self.config = ConfigManager.load_config()
        self.net = NetworkUtils()
        self.page_collector = SourceCollector(self.net, self.config)
        self.api_collector = GitHubSearchCollector(self.net, self.config)
        self.analyzer = RepoAnalyzer(self.net, self.config)
        
        ws = self.config.get("workspace_dir", "hunter_workspace")
        if not os.path.exists(ws):
            os.makedirs(ws)
        self.builder = BuilderRunner(ws)

    def run(self):
        Logger.log("=== Agent Hunter Started ===")
        
        # 1. Collect from multiple sources
        candidates_map = {}
        
        # A. Page Scraping
        page_candidates = self.page_collector.collect_candidates()
        candidates_map.update(page_candidates)
        
        # B. API Search (if enabled)
        if self.config.get("use_github_api", True):
            api_candidates = self.api_collector.collect_candidates()
            candidates_map.update(api_candidates)
            
        candidates = list(candidates_map.keys())
        
        # 2. Analyze & Filter
        Logger.log(f"Analyzing {len(candidates)} candidates...")
        valid_repos = []
        
        max_analyze = 30 
        for i, url in enumerate(candidates):
            if i >= max_analyze: break
            
            repo_data = self.analyzer.analyze(url)
            if repo_data:
                repo_data['source_origin'] = candidates_map.get(url, "Unknown")
                valid_repos.append(repo_data)
                
                print(f"  + Match: {repo_data['name']} ({repo_data['stars']} stars)")
                print(f"    URL:      {repo_data['url']}")
                print(f"    Created:  {repo_data['created_at']}")
                print(f"    Updated:  {repo_data['updated_at']}")
                print(f"    Source:   {repo_data['source_origin']}")
                print(f"    Purpose:  {repo_data['description'][:100]}..." if len(repo_data['description']) > 100 else f"    Purpose:  {repo_data['description']}")
                
                Logger.log(f"Match: {repo_data['name']} | Stars: {repo_data['stars']}")
                Logger.log(f"  > URL: {repo_data['url']}")
                Logger.log(f"  > Created: {repo_data['created_at']} | Updated: {repo_data['updated_at']}")
            
            time.sleep(0.5)
            
        valid_repos.sort(key=lambda x: x['score'], reverse=True)
        
        top_n = self.config.get("max_repos_to_process", 3)
        targets = valid_repos[:top_n]
        
        Logger.log(f"Selected top {len(targets)} repositories:")
        for t in targets:
            Logger.log(f"  - {t['name']} (Score: {t['score']:.1f})")

        # 3. Build & Run
        for t in targets:
            self.builder.process_repo(t)
            
        Logger.log("=== Agent Hunter Finished ===")
        
if __name__ == "__main__":
    agent = AgentHunter()
    agent.run()