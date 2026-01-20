#!/usr/bin/env python3
"""
Integrate QuickBooks settings UI into Project-R index.html
"""

import re

def integrate_quickbooks_ui():
    # Read the original index.html
    with open('/home/tasklet/project-r/static/index.html', 'r') as f:
        html = f.read()
    
    # Read the QuickBooks UI snippet
    with open('/home/tasklet/project-r/quickbooks_settings_ui.html', 'r') as f:
        qb_ui = f.read()
    
    # Extract CSS from QuickBooks UI
    css_match = re.search(r'<style>(.*?)</style>', qb_ui, re.DOTALL)
    qb_css = css_match.group(1) if css_match else ''
    
    # Extract HTML from QuickBooks UI (the settings screen div)
    html_match = re.search(r'<!-- Settings Screen with QuickBooks Integration -->.*?</div>\s*<!-- End Settings Screen -->', qb_ui, re.DOTALL)
    qb_html = html_match.group(0) if html_match else ''
    
    # Extract JavaScript from QuickBooks UI
    js_match = re.search(r'<script>(.*?)</script>', qb_ui, re.DOTALL)
    qb_js = js_match.group(1) if js_match else ''
    
    # 1. Add CSS before closing </style> tag
    html = html.replace('</style>', f'\n{qb_css}\n</style>')
    
    # 2. Add Settings screen HTML before closing </body> (find a good spot - before the scripts)
    # Find the last screen div and add after it
    last_screen_pattern = r'(<div id="updateData" class="screen">.*?</div>)'
    match = re.search(last_screen_pattern, html, re.DOTALL)
    if match:
        insert_pos = match.end()
        html = html[:insert_pos] + f'\n\n    {qb_html}\n' + html[insert_pos:]
    
    # 3. Add JavaScript before closing </script> tag or before </body>
    # Find the main script section
    script_pattern = r'(<script>)'
    if re.search(script_pattern, html):
        # Add at end of first script section
        html = re.sub(r'(</script>)', f'\n{qb_js}\n\\1', html, count=1)
    
    # Write the updated HTML
    with open('/home/tasklet/project-r/static/index.html', 'w') as f:
        f.write(html)
    
    print("✅ QuickBooks UI integrated successfully!")
    print("\nNext steps:")
    print("1. Add a Settings button to the navigation")
    print("2. Configure QuickBooks credentials")
    print("3. Deploy to Railway")

if __name__ == '__main__':
    integrate_quickbooks_ui()
