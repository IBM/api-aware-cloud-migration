from locust import LoadTestShape, HttpUser, task, between, events
import numpy as np
import resource
import pickle
import random
import math
import os
resource.setrlimit(resource.RLIMIT_NOFILE, (250000, 250000))


########################################################################################################################
# Simulation Configuration
########################################################################################################################
GLOBAL_NGINX_FRONTEND_URL  = 'http://9.59.197.200:32226/frontend'
GLOBAL_MEDIA_FRONTEND_URL  = 'http://9.59.197.200:32226/media'

GLOBAL_EXPERIMENT_DURATION = 60 * 10 * 31    # None = Run forever, 43200 = 12 hour
GLOBAL_SECONDS_PER_DAY     = 60* 10    # 3600 = 1 hour
GLOBAL_MIN_USERS           = 50
GLOBAL_PEAKS               = [150, 175, 200, 225, 250]
GLOBAL_RANDOMNESS          = 0.10
GLOBAL_WAIT_TIME           = between(1, 3)


########################################################################################################################
texts = [text.replace('@', '') for text in list(open('datasets/fb-posts/news.txt'))]
media = [os.path.join('datasets/inria-person', fname) for fname in os.listdir('datasets/inria-person')]
users = list(range(1, 963))
users_dummy_free = list(range(1000, 100000))
users_dummy_used = []
friendship = set()

cycle = 0
active_users, inactive_users = [], list(range(1, 963))
with open('datasets/social-graph/socfb-Reed98.mtx', 'r') as f:
    friends = {}
    for edge in f.readlines():
        edge = list(map(int, edge.strip().split()))
        if len(edge) == 0:
            continue
        if edge[0] not in friends:
            friends[edge[0]] = set()
        if edge[1] not in friends:
            friends[edge[1]] = set()
        friends[edge[0]].add(edge[1])
        friends[edge[1]].add(edge[0])
    friends = {user: list(l) for user, l in friends.items()}


########################################################################################################################
class LoadShape(LoadTestShape):
    peak_one_users = None
    peak_two_users = None
    second_of_day = None

    def tick(self):
        global cycle
        if GLOBAL_EXPERIMENT_DURATION is not None and round(self.get_run_time()) > GLOBAL_EXPERIMENT_DURATION:
            return None

        second_of_day = round(self.get_run_time()) % GLOBAL_SECONDS_PER_DAY
        if self.second_of_day is None or second_of_day < self.second_of_day:
            cycle += 1
            self.peak_one_users = random.choice(GLOBAL_PEAKS)
            self.peak_two_users = random.choice(GLOBAL_PEAKS)
        self.second_of_day = second_of_day

        user_count = (
                (self.peak_one_users - GLOBAL_MIN_USERS)
                * math.e ** -(((second_of_day / (GLOBAL_SECONDS_PER_DAY / 10 * 2 / 3)) - 5) ** 2)
                + (self.peak_two_users - GLOBAL_MIN_USERS)
                * math.e ** -(((second_of_day / (GLOBAL_SECONDS_PER_DAY / 10 * 2 / 3)) - 10) ** 2)
                + GLOBAL_MIN_USERS
        )
        max_offset = math.ceil(user_count * GLOBAL_RANDOMNESS)
        user_count += random.choice(list(range(-max_offset, max_offset + 1)))
        return round(user_count), round(min(user_count, 70))


class SocialNetworkUser(HttpUser):
    wait_time = GLOBAL_WAIT_TIME
    host = GLOBAL_NGINX_FRONTEND_URL
    local_cycle = cycle

    def check_cycle(self):
        if cycle == self.local_cycle:
            return

        tasks = []
        for func, weight in self.compositions[cycle % len(self.compositions)].items():
            tasks += [func] * weight
        self.tasks = tasks

        print('================= Cycle %d -> %d ====================' % (self.local_cycle, cycle))
        print({k: self.tasks.count(k) for k in self.compositions[cycle % len(self.compositions)]})

        self.local_cycle = cycle

    @task
    def login(self):
        self.check_cycle()

        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        user_id = random.choice(users)
        data = {'username': 'username_%d' % user_id,
                'password': 'password_%d' % user_id}

        self.client.post("/wrk2-api/user/login", data=data, headers=headers)

    @task
    def register(self):
        self.check_cycle()

        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        user = users_dummy_free.pop(0)
        users_dummy_used.append(user)
        data = {'first_name': 'first_name_%d' % user,
                'last_name': 'last_name_%d' % user,
                'username': 'username_%d' % user,
                'password': 'password_%d' % user,
                'user_id': user}

        self.client.post("/wrk2-api/user/register", data=data, headers=headers)

    @task
    def follow(self):
        self.check_cycle()

        if len(users_dummy_used) < 10:
            return
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        link = tuple(random.sample(users_dummy_used, 2))
        while link in friendship:
            link = tuple(random.sample(users_dummy_used, 2))
        friendship.add(link)
        user1, user2 = link
        self.client.post("/wrk2-api/user/follow", data={'user_id': user1, 'followee_id': user2}, headers=headers)

    @task
    def unfollow(self):
        self.check_cycle()

        if len(friendship) < 2:
            return
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        link = random.choice(tuple(friendship))
        friendship.remove(link)
        user1, user2 = link
        self.client.post("/wrk2-api/user/unfollow", data={'user_id': user1, 'followee_id': user2}, headers=headers)

    @task
    def readHomeTimeline(self):
        self.check_cycle()

        start = random.randint(0, 100)
        stop = start + 10

        response = self.client.get(
            "/wrk2-api/home-timeline/read?start=%s&stop=%s&user_id=%s" % (str(start), str(stop), str(self.user_id)),
            name="/wrk2-api/home-timeline/read")
        for post in eval(response.content):
            if len(post['media']) > 0:
                fname = post['media'][0]['media_id'] + '.' + post['media'][0]['media_type']
                self.client.get(
                    '%s/get-media?filename=%s' % (GLOBAL_MEDIA_FRONTEND_URL, fname),
                    name='/get-media')

    @task
    def readUserTimeline(self):
        self.check_cycle()

        start = random.randint(0, 100)
        stop = start + 10
        user_id = random.choice(friends[self.user_id])

        response = self.client.get(
            "/wrk2-api/user-timeline/read?start=%s&stop=%s&user_id=%s" % (str(start), str(stop), str(user_id)),
            name='/wrk2-api/user-timeline/read')
        for post in eval(response.content):
            if len(post['media']) > 0:
                fname = post['media'][0]['media_id'] + '.' + post['media'][0]['media_type']
                self.client.get(
                    '%s/get-media?filename=%s' % (GLOBAL_MEDIA_FRONTEND_URL, fname),
                    name='/get-media')

    def on_stop(self):
        active_users.remove(self.user_id)
        inactive_users.append(self.user_id)

    def on_start(self):
        self.check_cycle()
        self.user_id = random.choice(inactive_users)
        active_users.append(self.user_id)
        inactive_users.remove(self.user_id)

    compositions = [
        {login: 5, register: 50, follow: 15, unfollow: 5, readHomeTimeline: 10, readUserTimeline: 15},
        {login: 50, register: 10, follow: 5, unfollow: 15, readHomeTimeline: 5, readUserTimeline: 15},
        {login: 10, register: 5, follow: 50, unfollow: 15, readHomeTimeline: 15, readUserTimeline: 5},
        {login: 5, register: 15, follow: 5, unfollow: 50, readHomeTimeline: 15, readUserTimeline: 10},
        {login: 15, register: 15, follow: 10, unfollow: 5, readHomeTimeline: 50, readUserTimeline: 5},
        {login: 15, register: 5, follow: 15, unfollow: 10, readHomeTimeline: 5, readUserTimeline: 50},

        {login: 15, register: 10, follow: 5, unfollow: 15, readHomeTimeline: 50, readUserTimeline: 5},
        {login: 15, register: 5, follow: 15, unfollow: 5, readHomeTimeline: 10, readUserTimeline: 50},
        {login: 5, register: 15, follow: 15, unfollow: 50, readHomeTimeline: 5, readUserTimeline: 10},
        {login: 10, register: 15, follow: 50, unfollow: 5, readHomeTimeline: 15, readUserTimeline: 5},
        {login: 5, register: 50, follow: 5, unfollow: 10, readHomeTimeline: 15, readUserTimeline: 15},
        {login: 50, register: 5, follow: 10, unfollow: 15, readHomeTimeline: 5, readUserTimeline: 15},
    ]