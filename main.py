from blitspersecond import BlitsPerSecond, GameLoop


class dog_is_clean(GameLoop):
    def run(self):
        self.bps.loop = jump_in_pond()


class jump_in_pond(GameLoop):
    def run(self):
        self.bps.loop = wash_the_dog()


class wash_the_dog(GameLoop):
    def run(self):
        self.bps.loop = jump_in_pond()


bps = BlitsPerSecond()

bps.loop = dog_is_clean()  # can also pass loop_func
