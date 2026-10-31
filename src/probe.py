"""
VERSION: v1 - DataCenter IP Probe
"""
import urllib.request
import urllib.error

URLS = {
    "CheapCaribbean": "https://www.cheapcaribbean.com/",
    "Apple Vacations": "https://www.applevacations.com/",
    "Costco Travel": "https://www.costcotravel.com/",
    "Expedia": "https://www.expedia.com/"
}

def run_probe():
    print("Starting GitHub Actions Datacenter IP Probe...\n")
    for name, url in URLS.items():
        try:
            # Spoof a standard browser header
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            resp = urllib.request.urlopen(req, timeout=10)
            print(f"[✅ SUCCESS] {name}: HTTP {resp.getcode()} (Accessible)")
        except urllib.error.HTTPError as e:
            print(f"[❌ BLOCKED] {name}: HTTP {e.code} - {e.reason}")
        except Exception as e:
            print(f"[⚠️ ERROR] {name}: {str(e)}")
            
    print("\nProbe complete. If all sites are blocked, a proxy or self-hosted runner is required.")

if __name__ == "__main__":
    run_probe()
