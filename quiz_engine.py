difficulty="medium"

def update_difficulty(correct):

    global difficulty

    if correct:

        if difficulty=="medium":
            difficulty="hard"

        elif difficulty=="easy":
            difficulty="medium"

    else:

        if difficulty=="medium":
            difficulty="easy"

        elif difficulty=="hard":
            difficulty="medium"

    return difficulty